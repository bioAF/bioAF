"""API schemas for the literature-validation flow (lit_validation)."""

from datetime import datetime

from pydantic import BaseModel


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


class ClassifyRequest(BaseModel):
    """A human's by-hand verdict at the ``comparing`` gate (Phase 1 keeps comparison manual)."""

    classification: str


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


class ValidationStudySummary(BaseModel):
    """A lightweight row for the studies list (no plan/evidence join). ``confidence`` is derived from
    the classification the same way as the detail response, so the list can render the outcome badge."""

    id: int
    state: str
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
