"""Level-3 activation wiring (ADR-069 / spec-08).

Assemble ``evidence["level3"]`` from the ratified reproduction plan (the B2e differential design + the
B4 confirmed ground-truth finding claim), the analysis run's input file(s), and the matching builtin
headless template. This is the front-half glue that turns a Level-2 study into a Level-3 one: when
every piece is present, the driver's ``reproducing`` state runs the headless notebook and scores
concordance (E6). Any missing piece degrades honestly to Level-2, never a fabricated run, and says
which piece was missing (``Level3Decision.reason``) rather than leaving the answer in a server log.

Kept as a standalone function (not lit_validation-internal notebook logic) so it is unit-testable and
the driver stays orchestration glue.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.reproduction_plan import ReproductionPlan
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.notebook_service import _build_relative_path, _resolve_input_file_context
from app.services.reproduction_plan_service import validate_replicates

logger = logging.getLogger("bioaf.validation_level3")


@dataclass(frozen=True)
class InputRule:
    """One ordered attempt at locating a wiring entry's input file(s) among a run's outputs.

    ``path_segment`` matches a whole slash-delimited segment of the file's ``storage_uri``, never a
    substring: nf-core publishes ``star_salmon/`` and ``salmon/`` side by side, and a substring test
    for "salmon" matches both. Rules are tried in order and the first that matches anything wins, so
    a pipeline's primary output beats its secondary one deterministically.
    """

    filename_exact: str | None = None
    filename_contains: tuple[str, ...] = ()
    filename_excludes: tuple[str, ...] = ()
    filename_prefix_excludes: tuple[str, ...] = ()
    path_segment: str | None = None

    def matches(self, f: File) -> bool:
        name = (f.filename or "").lower()
        if self.filename_exact is not None and name != self.filename_exact.lower():
            return False
        if not all(n in name for n in self.filename_contains):
            return False
        if any(n in name for n in self.filename_excludes):
            return False
        if any(name.startswith(pre.lower()) for pre in self.filename_prefix_excludes):
            return False
        if self.path_segment is not None:
            segments = {s.lower() for s in (f.storage_uri or f.gcs_uri or "").split("/")}
            if self.path_segment.lower() not in segments:
                return False
        return True


@dataclass(frozen=True)
class Level3Wiring:
    """What reproducing a finding of one kind from one pipeline's output actually requires.

    Keyed on ``(pipeline_key, kind)``. The kind alone used to carry four unrelated concerns at once
    (the comparison family, which is a property of the FINDING; the output file and any transform,
    both properties of the PIPELINE; and the template), which works only while the four are 1:1. For
    rnaseq, chipseq and atacseq they are, so the coincidence got encoded as a law. scRNA-seq breaks
    it: its findings are ``gene`` findings, but its output is per-sample h5ad and it needs a
    pseudobulk transform to become the genes x samples matrix DESeq2 consumes.
    """

    template_notebook_path: str
    input_rules: tuple[InputRule, ...]
    # The template parameter that receives the mounted input path(s). Multi-file entries pass a
    # comma-separated list; the template splits it.
    path_parameter: str = "counts_path"
    # True: take EVERY file the winning rule matched (nf-core/scrnaseq emits one h5ad per sample).
    multiple: bool = False
    # An extra step between the pipeline's output and the DE matrix, carried out by the template.
    # Recorded on the bundle so a verdict can say how its matrix was built.
    transform: str | None = None
    id_column: str | None = None
    # The template parameter that receives the gene-id namespace the pseudobulk matrix should be
    # keyed by. Only nf-core/scrnaseq has the choice: its h5ad carries BOTH namespaces, so which one
    # to emit is decided per study, from the paper. Every other route's matrix has one id column and
    # no choice to make, and their templates do not declare the parameter.
    namespace_parameter: str | None = None


_SALMON_GENE_COUNTS = "salmon.merged.gene_counts.tsv"
# nf-core/atacseq and nf-core/chipseq publish a per-LIBRARY consensus matrix (mLb) and, when
# replicates are merged, a per-REPLICATE one (mRp). They have different column bases, and a bioAF
# differential design names libraries, so mLb is the correct one. mRp is the fallback for a run that
# published only that.
_CONSENSUS_PEAKS = ("consensus", "featurecounts")
# featureCounts writes `<matrix>.summary` beside every matrix it produces: same directory, same
# stem, and it carries the per-sample assignment counts rather than the peaks. It therefore contains
# every token the matrix does, so the contains-rules matched the PAIR and Level 3 refused with
# `ambiguous_input_file`. Study 13 hit this on the first real ATAC-seq Level-3 attempt, after the
# full 12-sample pipeline had already succeeded, so ~10 hours of compute was discarded at the last
# step by a sidecar. Excluded on every rule rather than the first, because any of them can match it.
_SIDECAR_EXCLUDES = (".summary",)
_PEAK_INPUT_RULES = (
    InputRule(filename_contains=(*_CONSENSUS_PEAKS, ".mlb."), filename_excludes=_SIDECAR_EXCLUDES),
    InputRule(filename_contains=_CONSENSUS_PEAKS, filename_excludes=(".mrp.", *_SIDECAR_EXCLUDES)),
    InputRule(filename_contains=_CONSENSUS_PEAKS, filename_excludes=_SIDECAR_EXCLUDES),
)

# The template is keyed by its exact notebook_path, NOT by category: the `differential_expression`
# category is shared with the interactive scRNA DE template (04_differential_expression.ipynb), so a
# category lookup can pick the wrong, non-headless notebook. Only the headless DESeq2 templates are
# valid Level-3 reproducers.
#
# Keys are matched EXACTLY. `scrnaseq` contains `rnaseq`, so a substring rule would hand an
# scRNA-seq study the bulk gene-count wiring.
_WIRING: dict[tuple[str, str], Level3Wiring] = {
    ("nf-core/rnaseq", "gene"): Level3Wiring(
        template_notebook_path="notebooks/de_bulk_deseq2.ipynb",
        # bioAF runs rnaseq with `aligner: star_salmon` AND `pseudo_aligner: salmon`, so both
        # directories publish a file of this name with different column bases. The declared aligner's
        # quantification is the primary result and wins; the pseudo-aligner's is the fallback.
        input_rules=(
            InputRule(filename_exact=_SALMON_GENE_COUNTS, path_segment="star_salmon"),
            InputRule(filename_exact=_SALMON_GENE_COUNTS, path_segment="salmon"),
            InputRule(filename_exact=_SALMON_GENE_COUNTS),
        ),
        id_column="gene_id",
    ),
    # scRNA-seq findings are `gene` findings, but nf-core/scrnaseq emits cells x genes per sample, not
    # genes x samples. `pseudobulk` is the route between the two: sum each sample's cells to one column.
    #
    # The per-sample files are used, never the concatenated `combined_*`: `concat_h5ad.py` calls
    # `ad.concat(..., label="sample")` with the file-path stem as the key, which OVERWRITES the clean
    # obs["sample"] the per-sample file already carried. The per-sample files need no grouping at all.
    #
    # CellBender's output is the same cell-called matrix with ambient RNA removed, so it is preferred
    # when present. `raw` is never an input: it is every barcode the sequencer saw, overwhelmingly
    # empty droplets, and summing it would pseudobulk the ambient soup along with the cells. A run with
    # no cell-called matrix refuses rather than falling back.
    ("nf-core/scrnaseq", "gene"): Level3Wiring(
        template_notebook_path="notebooks/de_pseudobulk_deseq2.ipynb",
        input_rules=(
            InputRule(filename_contains=("_cellbender_filter_matrix.h5ad",), filename_prefix_excludes=("combined_",)),
            InputRule(filename_contains=("_filtered_matrix.h5ad",), filename_prefix_excludes=("combined_",)),
        ),
        path_parameter="counts_paths",
        multiple=True,
        transform="pseudobulk",
        namespace_parameter="gene_id_namespace",
    ),
    # A microbiome finding is "these taxa changed", which is an id with a direction and a
    # significance: the same comparison family a DEG list is, so E6 needs no new code and the kind
    # stays `gene`.
    #
    # Verified from the source: DADA2_MERGE publishes into `${outdir}/dada2` (conf/modules.config
    # @ 2.18.0, lines 265-271) and emits `path("ASV_table.tsv")` (modules/local/dada2_merge.nf). The
    # script transposes the DADA2 sequence table, keys each row by an md5 of its sequence, reorders
    # to [ASV_ID, samples..., sequence], drops `sequence`, and writes tab-separated with
    # `row.names = FALSE`.
    #
    # `DADA2_table.tsv` is that same table WITH the trailing `sequence` column of raw nucleotides,
    # published beside it by the same process. Handed to DESeq2 as a matrix it would coerce that
    # column to NA and analyse a phantom sample, so the filename is matched exactly.
    #
    # Caveat, recorded rather than hidden: DESeq2 on ASV counts is defensible and widely done, but
    # it is not the microbiome field's consensus (compositional data and zero inflation are why
    # ANCOM-BC and ALDEx2 exist). A divergence here can be the method rather than the paper.
    ("nf-core/ampliseq", "gene"): Level3Wiring(
        template_notebook_path="notebooks/de_bulk_deseq2.ipynb",
        input_rules=(InputRule(filename_exact="ASV_table.tsv", path_segment="dada2"),),
        id_column="ASV_ID",
    ),
    # nf-core/smrnaseq needs no new notebook: `mirna.tsv` is a `miRNA` column followed by
    # per-sample integer counts, which is the shape de_bulk_deseq2 already consumes. Nothing in that
    # notebook is RNA-specific; it reads `counts_path`, takes `id_column`, and selects the named
    # sample columns.
    #
    # Verified against the source, not the output docs: `DATATABLE_MERGE` publishes into
    # `mirna_quant/mirtop/` with `pattern: "*.tsv"` (conf/modules.config @ 2.4.1, lines 485-491) and
    # emits exactly `path "mirna.tsv"` (modules/local/datatable_merge/main.nf), written by
    # `bin/collapse_mirtop.r` as `mirna = counts[, lapply(.SD, sum), by = miRNA]` with
    # `sep = "\t", row.names = FALSE`.
    #
    # The filename must be matched EXACTLY: two sibling processes publish into the same directory,
    # `PIVOT_WIDER` (`*joined_samples_mirtop.tsv`, long-form input to the merge) and `MIRTOP_EXPORT`
    # (`*_rawData.tsv`, per-sample). Neither has samples for columns, so a "*.tsv under mirtop" rule
    # would hand DESeq2 a table it would happily analyse and get wrong.
    ("nf-core/smrnaseq", "gene"): Level3Wiring(
        template_notebook_path="notebooks/de_bulk_deseq2.ipynb",
        input_rules=(InputRule(filename_exact="mirna.tsv", path_segment="mirtop"),),
        id_column="miRNA",
    ),
    ("nf-core/atacseq", "interval"): Level3Wiring(
        template_notebook_path="notebooks/da_peaks_deseq2.ipynb",
        input_rules=_PEAK_INPUT_RULES,
    ),
    ("nf-core/chipseq", "interval"): Level3Wiring(
        template_notebook_path="notebooks/da_peaks_deseq2.ipynb",
        input_rules=_PEAK_INPUT_RULES,
    ),
}


# FindingSet spells the namespaces one way and the pseudobulk template spells them another, because
# one is a taxonomy over deposited tables and the other is a switch between two columns of an h5ad.
_PAPER_NAMESPACE_TO_MATRIX = {"symbol": "symbol", "ensembl_gene": "ensembl"}


def matrix_namespace_for(paper_namespace: str | None) -> str:
    """Which namespace to key the reproduction matrix by, given the paper's confirmed finding set.

    nf-core/scrnaseq's h5ad carries exactly two: Ensembl ids as the matrix rownames and symbols in
    ``rowData$gene_symbol``. A paper in a THIRD namespace (entrez, miRBase) can be served by neither,
    and guessing a mapping here would manufacture agreement out of an id translation nobody checked;
    symbols are the honest attempt, and the concordance service's namespace guard is what refuses the
    comparison when the attempt does not match.
    """
    return _PAPER_NAMESPACE_TO_MATRIX.get((paper_namespace or "").strip().lower(), "symbol")


def supported_finding_kinds(pipeline_key: str | None) -> list[str]:
    """The finding kinds this pipeline can reproduce at Level-3, in a stable order.

    The single source of truth for what the C1 gate should offer. Empty means the pipeline maps,
    launches and produces QC, but no configured Level-3 route exists for it.
    """
    if not pipeline_key:
        return []
    order = ("gene", "interval")
    kinds = {k for (p, k) in _WIRING if p == pipeline_key}
    return [k for k in order if k in kinds]


@dataclass(frozen=True)
class Level3Decision:
    """Either the ``evidence["level3"]`` bundle, or why there is not one."""

    inputs: dict | None
    reason: str | None = None
    # A stable key for the decline path, so the UI and the tests can distinguish them without
    # matching on prose.
    reason_code: str | None = None


def _decline(study_id: int, code: str, reason: str) -> Level3Decision:
    logger.info("study %d: no Level-3 (%s): %s", study_id, code, reason)
    return Level3Decision(inputs=None, reason=reason, reason_code=code)


async def build_level3_inputs(
    session: AsyncSession, study: ValidationStudy, plan: ReproductionPlan | None
) -> dict | None:
    """Return the ``evidence["level3"]`` bundle for ``study``, or ``None`` for Level-2-only."""
    return (await resolve_level3(session, study, plan)).inputs


async def resolve_level3(
    session: AsyncSession, study: ValidationStudy, plan: ReproductionPlan | None
) -> Level3Decision:
    """Decide whether ``study`` can run Level-3, and say why not when it cannot.

    Requires all of: a confirmed finding claim (B4), a differential design with a fittable contrast
    (B2e), a completed analysis run, the input file(s) the wiring names, and the matching builtin
    template. Missing any one is an honest Level-2 degrade with a stated reason, not an error.
    """
    if plan is None:
        return _decline(study.id, "no_plan", "there is no reproduction plan for this study")

    claim = plan.finding_claim_json or {}
    finding_set = claim.get("finding_set")
    if not claim.get("confirmed") or not finding_set:
        return _decline(
            study.id,
            "no_finding_claim",
            "no ground-truth result set from the paper was confirmed, so there is nothing to reproduce against",
        )

    design = plan.differential_design_json or {}
    contrasts = design.get("contrasts") or []
    if not contrasts:
        return _decline(study.id, "no_contrast", "the reproduction plan declares no differential contrast to reproduce")

    # Re-check the replicate floor HERE, at the point of use, not only at the C1 gate. The gate is
    # bypassed two ways: `create_plan` writes the LLM's draft design straight onto the plan (so a design
    # the human never edited was never validated), and `_resolve_sample_design` rewrites the arms AFTER
    # the fetch, dropping picks that were not fetched -- a 3-vs-3 ratified at C1 becomes 1-vs-3 when two
    # samples are embargoed, and the `samples_mismatch` override returns to `setup` with no re-check.
    # This is the check that actually protects the run.
    replicate_errors = validate_replicates({"contrasts": contrasts[:1]})
    if replicate_errors:
        return _decline(study.id, "too_few_replicates", " ".join(replicate_errors))

    if study.analysis_run_id is None:
        return _decline(study.id, "no_analysis_run", "the study has no completed analysis run to reproduce from")

    kind = claim.get("kind") or "gene"
    pipeline_key = plan.pipeline_key
    wiring = _WIRING.get((pipeline_key or "", kind))
    if wiring is None:
        return _decline(
            study.id,
            "no_wiring",
            f"{pipeline_key or 'this pipeline'} has no configured route for reproducing a '{kind}' finding",
        )

    files, ambiguity = await _select_input_files(session, study.organization_id, study.analysis_run_id, wiring)
    if ambiguity:
        return _decline(study.id, "ambiguous_input_file", ambiguity)
    if not files:
        return _decline(
            study.id,
            "no_input_file",
            f"the analysis run published no file matching the input the {pipeline_key} '{kind}' route needs",
        )

    template = await _find_builtin_template(session, study.organization_id, wiring.template_notebook_path)
    if template is None:
        return _decline(
            study.id,
            "no_template",
            f"this bioAF instance has no '{wiring.template_notebook_path}' analysis template registered, "
            "so the reproduction could not be run here",
        )

    # The executor mounts each input file at /data/{relative_path}; the template reads them from the
    # wiring's path parameter.
    name_cache = await _resolve_input_file_context(session, {f.id: f for f in files})
    paths = [f"/data/{_build_relative_path(f, name_cache)}" for f in files]

    primary = contrasts[0]
    thresholds = claim.get("thresholds") or design.get("thresholds") or {}
    lfc = thresholds.get("log2fc")
    padj = thresholds.get("padj")

    test_samples = primary.get("test_samples") or []
    reference_samples = primary.get("reference_samples") or []
    parameters: dict = {
        wiring.path_parameter: ",".join(paths) if wiring.multiple else paths[0],
        "test_samples": ",".join(test_samples),
        "reference_samples": ",".join(reference_samples),
        "lfc_threshold": float(lfc) if lfc is not None else 1.0,
        "padj_threshold": float(padj) if padj is not None else 0.05,
    }
    if wiring.id_column:
        parameters["id_column"] = wiring.id_column
    # Ask the matrix for the namespace the PAPER used. Left unsent, the template's own default
    # decided, so a symbol-keyed paper met an Ensembl-keyed reproduction: zero overlap, refused by
    # the concordance's namespace guard, for a reason that has nothing to do with the science.
    if wiring.namespace_parameter:
        parameters[wiring.namespace_parameter] = matrix_namespace_for(finding_set.get("namespace"))

    # Matched-pairs / blocked design (ADR-069 item #2): flatten the per-sample subject map to a comma
    # list ALIGNED to the notebook's sample order (test then reference) so the DE template can build
    # `design = ~ block + condition`. Emit it only when every sample is labeled (the C1 gate guarantees
    # a balanced pairing when present); a partial/absent map degrades honestly to the unpaired design.
    subjects = primary.get("subjects") or {}
    ordered_samples = list(test_samples) + list(reference_samples)
    if subjects and ordered_samples and all(s in subjects for s in ordered_samples):
        parameters["block_labels"] = ",".join(subjects[s] for s in ordered_samples)

    return Level3Decision(
        inputs={
            "template_id": template.id,
            "parameters": parameters,
            "input_file_ids": [f.id for f in files],
            # Which file(s) the reproduction actually ran on. Provenance, not decoration: a verdict
            # that cannot name its input matrix cannot be re-baselined or challenged.
            "input_files": [f.filename for f in files],
            "transform": wiring.transform,
            "paper_finding_set": finding_set,
            "kind": kind,
            "contrast": primary.get("name"),
        }
    )


async def _select_input_files(
    session: AsyncSession, org_id: int, run_id: int, wiring: Level3Wiring
) -> tuple[list[File], str | None]:
    """Resolve the wiring's input rules against a run's outputs. Returns (files, ambiguity_reason).

    Rules are tried in declared order and the first that matches anything wins. For a single-file
    entry, two survivors is a REFUSAL, not a coin flip: this screens papers of unknown validity, and
    a stated refusal is always better than an unexplained pick.
    """
    rows = (
        (
            await session.execute(
                select(File)
                .where(File.source_pipeline_run_id == run_id, File.organization_id == org_id)
                .order_by(File.filename, File.id)
            )
        )
        .scalars()
        .all()
    )

    for rule in wiring.input_rules:
        matched = [f for f in rows if rule.matches(f)]
        if not matched:
            continue
        if wiring.multiple:
            return matched, None
        if len(matched) > 1:
            names = ", ".join(sorted({f.storage_uri or f.filename for f in matched}))
            return [], (
                f"the analysis run published {len(matched)} files that all look like the input matrix "
                f"({matched[0].filename}), and no rule separates them: {names}"
            )
        return matched, None
    return [], None


async def _find_builtin_template(session: AsyncSession, org_id: int, notebook_path: str) -> TemplateNotebook | None:
    return (
        (
            await session.execute(
                select(TemplateNotebook)
                .where(
                    TemplateNotebook.organization_id == org_id,
                    TemplateNotebook.notebook_path == notebook_path,
                    TemplateNotebook.is_builtin.is_(True),
                )
                .order_by(TemplateNotebook.id)
            )
        )
        .scalars()
        .first()
    )


# ---- plan_7 step 8: the same bundle, built from a deposit ----


@dataclass(frozen=True)
class DepositTemplate:
    """Which headless template reproduces a finding from a matrix of this kind of value.

    Chosen by what step 6 MEASURED, never by the filename and never by the pipeline. All three
    pre-plan_7 templates are DESeq2, which requires integer counts and estimates its own size
    factors: handing it TPM invalidates the dispersion model and returns numbers that are
    confidently wrong rather than obviously wrong.
    """

    template_notebook_path: str
    method: str


_DESEQ2_GENE = DepositTemplate("notebooks/de_bulk_deseq2.ipynb", "deseq2")
_DESEQ2_INTERVAL = DepositTemplate("notebooks/da_peaks_deseq2.ipynb", "deseq2")
_LIMMA = DepositTemplate("notebooks/de_normalized_limma.ipynb", "limma_trend")

# Every normalized flavour lands on limma-trend. `tpm_or_cpm` is what step 6 emits when it cannot
# separate the two from the matrix alone, and it does not need to: the choice of TEST is the same.
_NORMALIZED = ("tpm_or_cpm", "tpm", "cpm", "fpkm", "normalized_other", "log_transformed")


def template_for_value_type(value_type: str | None, *, kind: str = "gene") -> DepositTemplate | None:
    """The template for a deposited matrix of this measured value type, or None to refuse.

    `unknown` returns None rather than defaulting to counts. Defaulting would be the same defect as
    trusting the filename, one layer further on and with the consequence landing on a verdict.
    """
    v = (value_type or "").strip().lower()
    if v == "counts":
        return _DESEQ2_INTERVAL if kind == "interval" else _DESEQ2_GENE
    if v in _NORMALIZED:
        return _LIMMA
    return None


async def resolve_level3_from_deposit(
    session: AsyncSession,
    study: ValidationStudy,
    plan: ReproductionPlan | None,
    *,
    evidence: dict | None = None,
) -> Level3Decision:
    """Assemble ``evidence["level3"]`` from an acquired DEPOSIT rather than from a pipeline run.

    Returns the SAME bundle shape ``resolve_level3`` returns, because ``_handle_reproducing`` consumes
    it and is deliberately not modified by plan_7. That convergence is the whole design: the two
    routes differ in how they obtain a matrix, not in how a finding is reproduced from one.

    ``evidence`` is passed EXPLICITLY by the driver rather than read off the study, because the
    driver holds the freshly-measured inspection in a local dict and assigns ``evidence_json`` exactly
    once at the end. Reading the study here saw the pre-inspection evidence and declined every
    deposit with `unknown_value_type`. That is the same ordering trap `_handle_extracting` documents
    at length for its own bundle; making the input a parameter removes it rather than restating it.
    """
    if plan is None:
        return _decline(study.id, "no_plan", "the study has no reproduction plan")

    claim = plan.finding_claim_json or {}
    finding_set = claim.get("finding_set") or {}
    if not claim or not finding_set:
        return _decline(
            study.id,
            "no_finding_claim",
            "no ground-truth result set from the paper was confirmed, so there is nothing to reproduce against",
        )

    design = plan.differential_design_json or {}
    contrasts = design.get("contrasts") or []
    if not contrasts:
        return _decline(study.id, "no_contrast", "the reproduction plan declares no differential contrast to reproduce")

    replicate_errors = validate_replicates({"contrasts": contrasts[:1]})
    if replicate_errors:
        return _decline(study.id, "too_few_replicates", " ".join(replicate_errors))

    ev = evidence if evidence is not None else (study.evidence_json or {})
    deposit = ev.get("deposit") or {}
    matrices = [f for f in deposit.get("files") or [] if f.get("artifact_type") == "deposited_matrix"]
    if not matrices:
        return _decline(study.id, "no_deposit", "no deposited matrix was acquired for this study")

    inspection = ev.get("deposit_inspection") or {}
    kind = claim.get("kind") or "gene"
    template_spec = template_for_value_type(inspection.get("value_type_observed"), kind=kind)
    if template_spec is None:
        return _decline(
            study.id,
            "unknown_value_type",
            "the deposited matrix's values could not be identified as counts or as normalized "
            "values, and running either test on the wrong one would produce a confident wrong answer",
        )

    template = await _find_builtin_template(session, study.organization_id, template_spec.template_notebook_path)
    if template is None:
        return _decline(
            study.id,
            "no_template",
            f"this bioAF instance has no '{template_spec.template_notebook_path}' analysis template "
            "registered, so the reproduction could not be run here",
        )

    primary = contrasts[0]
    thresholds = claim.get("thresholds") or design.get("thresholds") or {}
    lfc = thresholds.get("log2fc")
    padj = thresholds.get("padj")
    test_samples = primary.get("test_samples") or []
    reference_samples = primary.get("reference_samples") or []

    files = await _load_deposit_files(session, study.organization_id, [m["file_id"] for m in matrices])
    if not files:
        return _decline(study.id, "no_deposit", "the acquired deposit files are no longer present")
    name_cache = await _resolve_input_file_context(session, {f.id: f for f in files})
    paths = [f"/data/{_build_relative_path(f, name_cache)}" for f in files]

    parameters: dict = {
        "counts_path": paths[0],
        "test_samples": ",".join(test_samples),
        "reference_samples": ",".join(reference_samples),
        "lfc_threshold": float(lfc) if lfc is not None else 1.0,
        "padj_threshold": float(padj) if padj is not None else 0.05,
        # A deposit's id column is whatever the depositor wrote, INCLUDING empty (GSE274331 leaves it
        # unnamed). The wiring's fixed id_column describes an nf-core output and cannot speak for a
        # deposit, so it is carried from what step 6 measured.
        "id_column": inspection.get("id_column") or "",
    }
    if template_spec.method == "limma_trend":
        # Logging a log compresses real differences into nothing and yields a quiet null result.
        parameters["already_logged"] = "true" if inspection.get("value_type_observed") == "log_transformed" else "false"

    subjects = primary.get("subjects") or {}
    ordered = list(test_samples) + list(reference_samples)
    if subjects and ordered and all(s in subjects for s in ordered):
        parameters["block_labels"] = ",".join(subjects[s] for s in ordered)

    return Level3Decision(
        inputs={
            "template_id": template.id,
            "parameters": parameters,
            "input_file_ids": [f.id for f in files],
            "input_files": [f.filename for f in files],
            "transform": None,
            "paper_finding_set": finding_set,
            "kind": kind,
            "contrast": primary.get("name"),
            # Which statistical test actually ran. A limma-trend result compared against a paper's
            # DESeq2 result is a METHOD difference, and attribution has to be able to name it rather
            # than charge the gap to the paper (the study-26 lesson).
            "method": template_spec.method,
            "source": "deposit",
        }
    )


async def _load_deposit_files(session: AsyncSession, org_id: int, file_ids: list[int]) -> list[File]:
    """The acquired deposit's File rows, in the order their ids were given."""
    if not file_ids:
        return []
    rows = (
        (await session.execute(select(File).where(File.id.in_(file_ids), File.organization_id == org_id)))
        .scalars()
        .all()
    )
    by_id = {f.id: f for f in rows}
    return [by_id[i] for i in file_ids if i in by_id]
