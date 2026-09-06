"""API schemas for the literature-validation flow (lit_validation)."""

from datetime import datetime

from pydantic import BaseModel, Field


class ValidationStudyRequest(BaseModel):
    """Request a validation for a paper (source captured even for ad-hoc papers)."""

    paper_id: int | None = None
    source_doi: str | None = None
    source_accession: str | None = None


class ReadRequest(BaseModel):
    """Text to read + plan. Optional: when omitted, B1 fetches the full text from the study's DOI;
    a pasted-in body overrides the fetch (and is the fallback when the paper is not open access)."""

    full_text: str | None = None


class DeclineRequest(BaseModel):
    reason: str | None = None


class DepositOverrideRequest(BaseModel):
    """ "Run it anyway" at the C1 gate, when the deposit's declared data type contradicts the plan.

    The reason is required and has a floor, because a one-click override becomes the default action
    and then the guard means nothing. It is kept on the study so a divergent verdict can be argued
    against the choice that produced it.
    """

    reason: str = Field(min_length=3, max_length=1000)


class ClassifyRequest(BaseModel):
    """A human's by-hand verdict at the ``comparing`` gate (Phase 1 keeps comparison manual)."""

    classification: str


class DifferentialDesignRequest(BaseModel):
    """B2e edit (Level-3): the human-ratified differential design at the C1 gate. An empty contrasts
    list clears the design (the plan stays Level-2)."""

    contrasts: list = []
    thresholds: dict | None = None
    # Which contrast of the ORIGINAL list the human was editing. Lets the plan tell a ratified model
    # choice from one a person overrode, instead of crediting the model for both.
    selected_contrast_index: int | None = None


class FindingSetRequest(BaseModel):
    """B4 (Level-3): the human confirms the paper's deposited result set at the C1 gate.

    ``table_text`` is the raw DEG/DA table (csv/tsv). ``kind`` selects the gene vs interval
    normalizer. ``contrast`` picks the columns of a multi-contrast wide table. Thresholds default to
    the paper's captured differential design when omitted.
    """

    kind: str  # "gene" | "interval"
    table_text: str
    contrast: str | None = None
    lfc_threshold: float | None = None
    padj_threshold: float | None = None
    source_locator: str | None = None
    # Which column plays which role, when the alias list did not recognise the header. Posted
    # back by the gate's column picker; absent on the first attempt.
    column_map: dict | None = None


class SampleManifestEntry(BaseModel):
    """One recognizable sample for the Level-3 picker: what the scientist reads (title + condition)
    plus the accessions the picker stores + the resolver later maps to a fetched Sample."""

    experiment_accession: str = ""
    run_accession: str = ""
    sample_accession: str = ""
    title: str = ""
    condition: str = ""


class SampleManifestResponse(BaseModel):
    """The study's per-sample manifest for the Level-3 picker, or an explicit unavailable reason (200,
    never a 500) so the gate degrades to free-text sample entry."""

    samples: list[SampleManifestEntry] = []
    unavailable_reason: str | None = None


class ComparisonTargetResponse(BaseModel):
    metric_key: str
    claimed_value: float | None = None
    unit: str | None = None
    tolerance: float | None = None
    source_locator: str | None = None


class ReproductionPlanResponse(BaseModel):
    id: int
    accessions: list | None = None
    sample_sheet: dict | None = None
    pipeline_key: str | None = None
    pipeline_version: str | None = None
    parameters: dict | None = None
    # The tools the paper's own methods named. Visible on the plan because it is what an attributed
    # divergence is argued from, so a human ratifying a verdict can check the argument's input.
    tools: list | None = None
    # Where the authors said their analysis code lives (plan_7 step 3). Shown at the C1 gate so a
    # scientist can weigh the reproduction against the authors' own code; never executed.
    code_availability: list | None = None
    differential_design: dict | None = None
    finding_claim: dict | None = None
    reference_genome: str | None = None
    reference_build: str | None = None
    mapping_confidence: str | None = None
    mapping_notes: str | None = None
    blockers: list | None = None
    extractor_model: str | None = None
    extractor_provider: str | None = None
    comparison_targets: list[ComparisonTargetResponse] = []
    # Computed from the targets: what the model decided about each claim, why, how sure it was, and
    # which model decided. Rendered at the C1 gate in BOTH autonomy modes, because an AI decision
    # that cannot be attributed is a defect rather than a feature (plan_6 step 5).
    ai_decisions: list[dict] = []
    # Computed, not stored: which Level-3 finding kinds this plan's pipeline actually has a route for.
    # The C1 gate offered both kinds for every plan_ready study with no pipeline check, so a scientist
    # could paste a DEG table for an ATAC study and spend hours of compute to learn there was never a
    # route. Empty means the pipeline maps, launches and produces QC, but reproduces no finding.
    supported_finding_kinds: list[str] = []
    # Computed, not stored: the ONE blocker that refuses approval, and the pipeline that would
    # resolve it. A plan's blockers are mostly advisory (two reference builds named, sample ids
    # missing) and exactly one is fatal; rendered as one bullet among the rest, a scientist learned
    # which by clicking Approve and reading a 400. None when the plan carries no such conflict.
    deposit_conflict: dict | None = None
    # Computed, not stored: whether this org's catalog actually holds the plan's pipeline. Install
    # state changes under a plan's feet, so storing it would go stale. None when the plan names no
    # pipeline, because "not installed" would be a wrong answer to a question nobody asked.
    pipeline_installed: bool | None = None
    # The bare nf-core registry name (`ampliseq`), which is what the install endpoint takes. Carried
    # so the gate's install action never has to parse it back out of the key.
    pipeline_registry_name: str | None = None


class ValidationStudySummary(BaseModel):
    """A lightweight row for the studies list (no plan/evidence join). ``confidence`` is derived from
    the classification the same way as the detail response, so the list can render the outcome badge."""

    id: int
    state: str
    # Display title resolved server-side: the source paper's title -> DOI -> accession -> "Study #{id}"
    # (so a scientist scanning the list sees which paper each study reproduces, not a bare id).
    title: str = ""
    classification: str | None = None
    confidence: float | None = None
    paper_id: int | None = None
    source_doi: str | None = None
    source_accession: str | None = None
    experiment_id: int | None = None
    created_at: datetime | None = None


class ValidationStudyResponse(BaseModel):
    id: int
    state: str
    # Display title resolved server-side via the paper.title -> DOI -> accession -> "Study #{id}" ladder.
    title: str = ""
    classification: str | None = None
    # "% confident the results were validated" (0-100), derived from the classification for the UI status
    # badge (frontend lib/validationStatus). None = validation could not be run/concluded, or the study
    # is not yet classified -> rendered as "Could Not Reproduce". Interim mapping until E2 (see
    # models.validation_study.classification_confidence).
    confidence: float | None = None
    paper_id: int | None = None
    source_doi: str | None = None
    source_accession: str | None = None
    experiment_id: int | None = None
    reproduction_plan_id: int | None = None
    approved_by_user_id: int | None = None
    failure_reason: str | None = None
    plan: ReproductionPlanResponse | None = None
    # The assembled evidence bundle (computed QC metrics beside the paper's claimed targets, plus the
    # linked run ids) the human reads to classify by hand at the comparing gate. Null until extracting.
    evidence: dict | None = None
