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
from dataclasses import dataclass, field

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
    path_segment: str | None = None

    def matches(self, f: File) -> bool:
        name = (f.filename or "").lower()
        if self.filename_exact is not None and name != self.filename_exact.lower():
            return False
        if not all(n in name for n in self.filename_contains):
            return False
        if any(n in name for n in self.filename_excludes):
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
    extra_parameters: dict = field(default_factory=dict)


_SALMON_GENE_COUNTS = "salmon.merged.gene_counts.tsv"
# nf-core/atacseq and nf-core/chipseq publish a per-LIBRARY consensus matrix (mLb) and, when
# replicates are merged, a per-REPLICATE one (mRp). They have different column bases, and a bioAF
# differential design names libraries, so mLb is the correct one. mRp is the fallback for a run that
# published only that.
_CONSENSUS_PEAKS = ("consensus", "featurecounts")
_PEAK_INPUT_RULES = (
    InputRule(filename_contains=(*_CONSENSUS_PEAKS, ".mlb.")),
    InputRule(filename_contains=_CONSENSUS_PEAKS, filename_excludes=(".mrp.",)),
    InputRule(filename_contains=_CONSENSUS_PEAKS),
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
    ("nf-core/atacseq", "interval"): Level3Wiring(
        template_notebook_path="notebooks/da_peaks_deseq2.ipynb",
        input_rules=_PEAK_INPUT_RULES,
    ),
    ("nf-core/chipseq", "interval"): Level3Wiring(
        template_notebook_path="notebooks/da_peaks_deseq2.ipynb",
        input_rules=_PEAK_INPUT_RULES,
    ),
}


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
        **wiring.extra_parameters,
    }
    if wiring.id_column:
        parameters["id_column"] = wiring.id_column

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
