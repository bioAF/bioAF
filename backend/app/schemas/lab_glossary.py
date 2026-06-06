from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.experiment import UserSummary


class LabGlossaryTermResponse(BaseModel):
    id: int
    term: str
    definition: str
    aliases: list[str] | None = None
    category: str | None = None
    context: str | None = None
    source: str
    created_by: UserSummary | None = None
    created_at: datetime
    updated_at: datetime


class LabGlossaryTermListResponse(BaseModel):
    terms: list[LabGlossaryTermResponse]
    total: int
    page: int
    page_size: int


class LabGlossaryTermCreate(BaseModel):
    term: str = Field(..., max_length=500)
    definition: str
    aliases: list[str] | None = None
    category: str | None = Field(default=None, max_length=200)
    context: str | None = None


class LabGlossaryTermUpdate(BaseModel):
    term: str | None = Field(default=None, max_length=500)
    definition: str | None = None
    aliases: list[str] | None = None
    category: str | None = Field(default=None, max_length=200)
    context: str | None = None


# --- scan / review -----------------------------------------------------------


class LabGlossaryScanRequest(BaseModel):
    # ``topic`` is gone (LK-SPEC-D, D1); ``experiment`` reuses the Experiment
    # Review context. ``import`` is created via the dedicated CSV endpoint.
    scan_type: Literal["experiment", "document", "platform_wide"]
    scan_input: str | None = None


class LabGlossaryScanJobResponse(BaseModel):
    id: int
    scan_type: str
    scan_input: str | None = None
    status: str
    proposed_new_count: int | None = None
    proposed_changed_count: int | None = None
    accepted_count: int | None = None
    rejected_count: int | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class LabGlossaryProposalResponse(BaseModel):
    id: int
    term: str
    proposed_definition: str
    proposed_aliases: list[str] | None = None
    proposed_category: str | None = None
    proposed_context: str | None = None
    proposal_type: str
    existing_term_id: int | None = None
    existing_definition: str | None = None
    source_description: str | None = None
    previously_rejected: bool
    review_status: str


class LabGlossaryProposalListResponse(BaseModel):
    job: LabGlossaryScanJobResponse
    new_terms: list[LabGlossaryProposalResponse]
    changed_terms: list[LabGlossaryProposalResponse]


class LabGlossaryProposalDecision(BaseModel):
    proposal_id: int
    decision: Literal["accepted", "rejected", "kept_existing"]


class LabGlossaryReviewRequest(BaseModel):
    decisions: list[LabGlossaryProposalDecision] = []
    accept_all_remaining: bool = False
    reject_all_remaining: bool = False


class LabGlossaryReviewResponse(BaseModel):
    accepted: int
    rejected: int
    kept_existing: int


class LabGlossaryPendingResponse(BaseModel):
    pending_review_count: int
    # Scan/import job ids that still have proposals awaiting review, most recent
    # first, so the pending banner can open the review flow.
    job_ids: list[int] = []
