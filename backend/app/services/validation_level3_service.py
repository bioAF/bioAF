"""Level-3 activation wiring (ADR-069 / spec-08).

Assemble ``evidence["level3"]`` from the ratified reproduction plan (the B2e differential design + the
B4 confirmed ground-truth finding claim), the analysis run's count-matrix file, and the matching
builtin headless template. This is the front-half glue that turns a Level-2 study into a Level-3 one:
when every piece is present, the driver's ``reproducing`` state runs the headless notebook and scores
concordance (E6). Any missing piece degrades honestly to Level-2 (returns ``None``), never a
fabricated run.

Kept as a standalone function (not lit_validation-internal notebook logic) so it is unit-testable and
the driver stays orchestration glue.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.models.reproduction_plan import ReproductionPlan
from app.models.template_notebook import TemplateNotebook
from app.models.validation_study import ValidationStudy
from app.services.notebook_service import _build_relative_path, _resolve_input_file_context
from app.services.reproduction_plan_service import validate_replicates

logger = logging.getLogger("bioaf.validation_level3")

# Per finding-kind wiring: which builtin template reproduces the finding, how to recognize the
# count-matrix file among the analysis run's outputs, and the id column the template reads. RNA-seq DE
# runs on salmon's gene-count matrix; ATAC/ChIP DA runs on the consensus-peak featureCounts matrix.
#
# The template is keyed by its exact notebook_path, NOT by category: the `differential_expression`
# category is shared with the interactive scRNA DE template (04_differential_expression.ipynb), so a
# category lookup can pick the wrong, non-headless notebook. Only the headless bulk/peak DESeq2
# templates are valid Level-3 reproducers.
_TYPE_WIRING: dict[str, dict] = {
    "gene": {
        "template_notebook_path": "notebooks/de_bulk_deseq2.ipynb",
        "count_matrix_exact": "salmon.merged.gene_counts.tsv",
        "id_column": "gene_id",
    },
    "interval": {
        "template_notebook_path": "notebooks/da_peaks_deseq2.ipynb",
        "count_matrix_contains": ("consensus", "featurecounts"),
        "id_column": None,
    },
}


async def build_level3_inputs(
    session: AsyncSession, study: ValidationStudy, plan: ReproductionPlan | None
) -> dict | None:
    """Return the ``evidence["level3"]`` bundle for ``study``, or ``None`` for Level-2-only.

    Requires all of: a confirmed finding claim (B4), a differential design with a contrast (B2e), a
    completed analysis run, its count-matrix file, and the matching builtin template. Missing any one
    is an honest Level-2 degrade, logged, not an error.
    """
    if plan is None:
        return None

    claim = plan.finding_claim_json or {}
    finding_set = claim.get("finding_set")
    if not claim.get("confirmed") or not finding_set:
        return None

    design = plan.differential_design_json or {}
    contrasts = design.get("contrasts") or []
    if not contrasts:
        return None

    # Re-check the replicate floor HERE, at the point of use, not only at the C1 gate. The gate is
    # bypassed two ways: `create_plan` writes the LLM's draft design straight onto the plan (so a design
    # the human never edited was never validated), and `_resolve_sample_design` rewrites the arms AFTER
    # the fetch, dropping picks that were not fetched -- a 3-vs-3 ratified at C1 becomes 1-vs-3 when two
    # samples are embargoed, and the `samples_mismatch` override returns to `setup` with no re-check.
    # This is the check that actually protects the run.
    replicate_errors = validate_replicates({"contrasts": contrasts[:1]})
    if replicate_errors:
        logger.info("study %d: %s; staying Level-2", study.id, " ".join(replicate_errors))
        return None

    if study.analysis_run_id is None:
        return None

    kind = claim.get("kind") or "gene"
    wiring = _TYPE_WIRING.get(kind)
    if wiring is None:
        logger.info("study %d: no Level-3 wiring for kind '%s'; staying Level-2", study.id, kind)
        return None

    count_file = await _find_count_matrix(session, study.organization_id, study.analysis_run_id, wiring)
    if count_file is None:
        logger.info(
            "study %d: no count-matrix file for the %s finding on run %s; staying Level-2",
            study.id,
            kind,
            study.analysis_run_id,
        )
        return None

    template = await _find_builtin_template(session, study.organization_id, wiring["template_notebook_path"])
    if template is None:
        logger.info(
            "study %d: no builtin '%s' template registered; staying Level-2",
            study.id,
            wiring["template_notebook_path"],
        )
        return None

    # The executor mounts the input file at /data/{relative_path}; the template reads counts_path.
    name_cache = await _resolve_input_file_context(session, {count_file.id: count_file})
    counts_path = f"/data/{_build_relative_path(count_file, name_cache)}"

    primary = contrasts[0]
    thresholds = claim.get("thresholds") or design.get("thresholds") or {}
    lfc = thresholds.get("log2fc")
    padj = thresholds.get("padj")

    test_samples = primary.get("test_samples") or []
    reference_samples = primary.get("reference_samples") or []
    parameters: dict = {
        "counts_path": counts_path,
        "test_samples": ",".join(test_samples),
        "reference_samples": ",".join(reference_samples),
        "lfc_threshold": float(lfc) if lfc is not None else 1.0,
        "padj_threshold": float(padj) if padj is not None else 0.05,
    }
    if wiring.get("id_column"):
        parameters["id_column"] = wiring["id_column"]

    # Matched-pairs / blocked design (ADR-069 item #2): flatten the per-sample subject map to a comma
    # list ALIGNED to the notebook's sample order (test then reference) so the DE template can build
    # `design = ~ block + condition`. Emit it only when every sample is labeled (the C1 gate guarantees
    # a balanced pairing when present); a partial/absent map degrades honestly to the unpaired design.
    subjects = primary.get("subjects") or {}
    ordered_samples = list(test_samples) + list(reference_samples)
    if subjects and ordered_samples and all(s in subjects for s in ordered_samples):
        parameters["block_labels"] = ",".join(subjects[s] for s in ordered_samples)

    return {
        "template_id": template.id,
        "parameters": parameters,
        "input_file_ids": [count_file.id],
        "paper_finding_set": finding_set,
        "kind": kind,
        "contrast": primary.get("name"),
    }


async def _find_count_matrix(session: AsyncSession, org_id: int, run_id: int, wiring: dict) -> File | None:
    rows = (
        (
            await session.execute(
                select(File).where(File.source_pipeline_run_id == run_id, File.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    )

    exact = wiring.get("count_matrix_exact")
    if exact:
        for f in rows:
            if f.filename == exact:
                return f
        return None

    needles = wiring.get("count_matrix_contains") or ()
    for f in rows:
        name = (f.filename or "").lower()
        if all(n in name for n in needles):
            return f
    return None


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
