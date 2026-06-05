"""SDR API schemas (ADR-063, ADR-064)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.experiment import UserSummary


class SdrCategoryResponse(BaseModel):
    id: int
    name: str


class SdrCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class SdrTransitionResponse(BaseModel):
    id: int
    from_status: str
    to_status: str
    note: str | None = None
    transitioned_by: UserSummary | None = None
    transitioned_at: datetime


class SdrSupersessionLink(BaseModel):
    id: int
    sdr_number: int
    title: str
    status: str


class SdrSummary(BaseModel):
    """Row shape for the browser list."""

    id: int
    sdr_number: int
    title: str
    status: str
    category: SdrCategoryResponse | None = None
    owner: UserSummary | None = None
    trigger_date: date | None = None
    created_at: datetime
    updated_at: datetime


class SdrListResponse(BaseModel):
    sdrs: list[SdrSummary]
    total: int
    page: int
    page_size: int


class SdrDetailResponse(SdrSummary):
    decision: str
    justification: str
    created_by: UserSummary | None = None
    trigger_warning_sent_at: datetime | None = None
    superseded_by: SdrSupersessionLink | None = None
    supersedes: SdrSupersessionLink | None = None
    transitions: list[SdrTransitionResponse] = []


class SdrCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category_id: int | None = None
    decision: str = Field(..., min_length=1)
    justification: str = Field(..., min_length=1)
    trigger_date: date | None = None


class SdrUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    # category_id / trigger_date are nullable AND optional: use the *_set flags to
    # tell "clear it" from "leave unchanged". model_fields_set drives this in the API.
    category_id: int | None = None
    decision: str | None = None
    justification: str | None = None
    trigger_date: date | None = None


class SdrTransitionRequest(BaseModel):
    to_status: Literal["active", "flagged_for_review", "superseded", "repealed"]
    note: str | None = None
    superseded_by_sdr_id: int | None = None


class SdrOwnerReassignRequest(BaseModel):
    owner_user_id: int
