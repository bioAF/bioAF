"""Level-3 headless differential-analysis templates (lit_validation, ADR-069).

The DE/DA templates are R notebooks (kernelspec 'ir') run by the headless executor. Their
parameters must be str/number only, because the parameters-cell injector emits Python literals
into the (R) cell, and None/True/False are not valid R.
"""

import json

from app.services.template_notebook_service import (
    BUILTIN_TEMPLATES,
    PACKAGE_TEMPLATES_DIR,
    TemplateNotebookService,
)

_L3 = ["de_bulk_deseq2.ipynb", "da_peaks_deseq2.ipynb", "de_pseudobulk_deseq2.ipynb"]


def test_level3_template_files_ship_in_package():
    # Must live in the package dir so they ship inside the backend image (not just the repo scripts/).
    for f in _L3:
        p = PACKAGE_TEMPLATES_DIR / f
        assert p.exists(), f"missing template {p}"
        nb = json.loads(p.read_text())
        assert nb["metadata"]["kernelspec"]["name"] == "ir"
        assert any("parameters" in c.get("metadata", {}).get("tags", []) for c in nb["cells"])


def test_level3_templates_registered_builtin():
    paths = {t["notebook_path"] for t in BUILTIN_TEMPLATES}
    assert "notebooks/de_bulk_deseq2.ipynb" in paths
    assert "notebooks/da_peaks_deseq2.ipynb" in paths


def test_de_template_supports_a_paired_block_design():
    # ADR-069 item #2: with a per-sample block label the DE notebook must build `~ block + condition`
    # (cancels donor-to-donor baseline variance), and fall back to `~ condition` when unpaired.
    nb = json.loads((PACKAGE_TEMPLATES_DIR / "de_bulk_deseq2.ipynb").read_text())
    params_cell = next(c for c in nb["cells"] if "parameters" in c.get("metadata", {}).get("tags", []))
    assert "block_labels" in "".join(params_cell["source"])
    src = "\n".join("".join(c.get("source", [])) for c in nb["cells"])
    assert "block + condition" in src  # paired design
    assert "~ condition" in src  # unpaired fallback preserved
    # The injector rebuilds the parameters cell from the merged param dict, and a template row seeded
    # before block_labels existed will not inject it, so the notebook must self-default the optional
    # param or the design cell hits `object 'block_labels' not found` (caught live on the demo).
    assert 'exists("block_labels")' in src


def test_level3_params_inject_to_valid_r():
    for tmpl in BUILTIN_TEMPLATES:
        if not tmpl["notebook_path"].endswith(tuple(_L3)):
            continue
        nb = {"cells": [{"cell_type": "code", "metadata": {"tags": ["parameters"]}, "source": ["x = 1\n"]}]}
        injected = TemplateNotebookService._inject_parameters(nb, tmpl["parameters"])
        src = "".join(injected["cells"][0]["source"])
        # Python-literal injection must not emit R-invalid tokens for these templates.
        assert "None" not in src and "True" not in src and "False" not in src
        assert "padj_threshold = 0.05" in src


# --- the pseudobulk template (nf-core/scrnaseq -> genes x samples -> DESeq2) ---

_PSEUDOBULK = "de_pseudobulk_deseq2.ipynb"


def _declared_parameters(notebook_path: str) -> dict:
    """The parameter dict a builtin template declares, by notebook_path."""
    tmpl = next(t for t in BUILTIN_TEMPLATES if t["notebook_path"] == notebook_path)
    params = tmpl["parameters"]
    assert isinstance(params, dict)
    return params


def _source(basename):
    nb = json.loads((PACKAGE_TEMPLATES_DIR / basename).read_text())
    return nb, "\n".join("".join(c.get("source", [])) for c in nb["cells"])


def test_pseudobulk_template_registered_builtin_for_scrnaseq():
    tmpl = next(t for t in BUILTIN_TEMPLATES if t["notebook_path"] == f"notebooks/{_PSEUDOBULK}")
    assert tmpl["compatible_with"] == "nf-core/scrnaseq"
    assert (PACKAGE_TEMPLATES_DIR / _PSEUDOBULK).exists()


def test_pseudobulk_output_is_named_like_the_bulk_result_table():
    """`_read_reproduction_output` picks among a session's output files by scoring the name against
    ("finding", "result", "de_", "diff") and then tabular-ness. A name outside that set scores low and
    the WRONG file gets read as the reproduced set, which looks like a scientific divergence."""
    assert _declared_parameters(f"notebooks/{_PSEUDOBULK}")["output_path"] == "/outputs/de_results.csv"

    from app.services.validation_driver_service import ValidationDriverService  # noqa: F401

    name = "de_results.csv"
    assert any(t in name for t in ("finding", "result", "de_", "diff"))
    assert name.endswith(".csv")


def test_pseudobulk_declares_every_parameter_the_wiring_injects():
    """The injector REBUILDS the parameters cell from the template's stored parameter dict merged with
    the overrides, so a parameter the template does not declare is only defined when the wiring
    happens to inject it. Anything conditional (block_labels) must also self-default in the body."""
    from app.services.validation_level3_service import _WIRING

    wiring = _WIRING[("nf-core/scrnaseq", "gene")]
    declared = set(_declared_parameters(wiring.template_notebook_path))
    injected = {
        wiring.path_parameter,
        "test_samples",
        "reference_samples",
        "lfc_threshold",
        "padj_threshold",
        "block_labels",
    }
    assert injected <= declared


def test_every_wiring_entry_points_at_a_template_that_declares_its_parameters():
    """Guards the whole dict, not just the new entry: adding a route with a mismatched parameter name
    is exactly the class of mistake the (pipeline, kind) re-key exists to make visible."""
    from app.services.validation_level3_service import _WIRING

    for (pipeline, kind), wiring in _WIRING.items():
        registered = [t for t in BUILTIN_TEMPLATES if t["notebook_path"] == wiring.template_notebook_path]
        assert registered, f"{pipeline}/{kind} names an unregistered template"
        declared = set(_declared_parameters(wiring.template_notebook_path))
        always = {wiring.path_parameter, "test_samples", "reference_samples", "lfc_threshold", "padj_threshold"}
        assert always <= declared, f"{pipeline}/{kind}: template misses {always - declared}"
        if wiring.id_column:
            assert "id_column" in declared


def test_pseudobulk_uses_only_cell_called_matrices():
    """`raw` is every barcode the sequencer saw, mostly empty droplets holding ambient RNA. The
    notebook must never be pointed at one, and must say which input type it read."""
    _, src = _source(_PSEUDOBULK)
    assert "h5ad" in src
    assert "readH5AD" in src  # zellkonverter, which is in the image's build-time req guard


def test_pseudobulk_asserts_one_sample_per_file():
    """Each per-sample h5ad carries obs['sample'] set at creation, so the whole file sums to one
    column. A file that carries more than one sample label is not what this notebook assumes and must
    fail loudly rather than silently pool two samples into one pseudobulk column."""
    _, src = _source(_PSEUDOBULK)
    assert "sample" in src
    assert "stop(" in src


def test_pseudobulk_fails_loudly_on_a_sample_the_matrix_does_not_have():
    """A silent subset would run DESeq2 on fewer samples than the design declares and report a verdict
    for a contrast nobody asked for. The stop must name BOTH the requested and the observed names."""
    _, src = _source(_PSEUDOBULK)
    assert "setdiff(samples, colnames(" in src
    assert "requested" in src and "observed" in src


def test_pseudobulk_reuses_the_bulk_deseq2_contract():
    """The only new code is the read-and-aggregate head; the statistical core is the proven one. Both
    templates must run the same contrast and emit the same normalizer-compatible columns."""
    _, pseudo = _source(_PSEUDOBULK)
    _, bulk = _source("de_bulk_deseq2.ipynb")
    for fragment in (
        'contrast = c("condition", "test", "reference")',
        "gene_id = ",
        "log2FoldChange = res$log2FoldChange",
        "padj = res$padj",
        "~ block + condition",
        "~ condition",
    ):
        assert fragment in bulk, f"bulk template no longer contains {fragment!r}"
        assert fragment in pseudo, f"pseudobulk template must match the bulk contract: {fragment!r}"
    assert 'exists("block_labels")' in pseudo


def test_pseudobulk_sums_raw_counts_and_states_its_gene_namespace():
    """DESeq2 requires integer counts and applies its own size factors, so the aggregation must sum
    raw counts, not normalized values. And two correct gene sets in different namespaces overlap by
    zero, which reads as a scientific divergence while being purely technical, so the notebook states
    which namespace it emitted."""
    _, src = _source(_PSEUDOBULK)
    assert "rowSums" in src
    assert "as.integer" in src
    assert "gene_id_namespace" in src
    assert "namespace" in src.lower()


def test_pseudobulk_refuses_two_files_carrying_the_same_sample():
    """`columns[[sample_name]] <- ...` overwrites on a repeat, so two files for one sample would
    silently drop one of them and pseudobulk the other twice as that sample."""
    _, src = _source(_PSEUDOBULK)
    assert "%in% names(columns)" in src


def test_pseudobulk_reports_the_namespace_it_actually_used():
    """Asking for ensembl on a matrix with no rowData$gene_ids falls back to symbols. Printing the
    REQUESTED namespace there would describe the output wrongly, and a namespace mismatch is the one
    failure that looks like a scientific divergence while being purely technical."""
    _, src = _source(_PSEUDOBULK)
    assert "used_namespace" in src
