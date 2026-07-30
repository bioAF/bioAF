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

_L3 = ["de_bulk_deseq2.ipynb", "da_peaks_deseq2.ipynb"]


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
