"""API schemas for the literature-validation flow (lit_validation)."""

from pydantic import BaseModel


class ValidationStudyRequest(BaseModel):
    """Request a validation for a paper (source captured even for ad-hoc papers)."""

    paper_id: int | None = None
    source_doi: str | None = None
    source_accession: str | None = None


class ReadRequest(BaseModel):
    """Full text to read + plan (B1 supplies this; here it can be pasted in)."""

    full_text: str


class DeclineRequest(BaseModel):
    reason: str | None = None


class ClassifyRequest(BaseModel):
    """A human's by-hand verdict at the ``comparing`` gate (Phase 1 keeps comparison manual)."""

    classification: str


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
    reference_genome: str | None = None
    reference_build: str | None = None
    mapping_confidence: str | None = None
    mapping_notes: str | None = None
    blockers: list | None = None
    extractor_model: str | None = None
    extractor_provider: str | None = None
    comparison_targets: list[ComparisonTargetResponse] = []


class ValidationStudyResponse(BaseModel):
    id: int
    state: str
    classification: str | None = None
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
