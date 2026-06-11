"""Pydantic request/response schemas for the Literature REST API (ADR-056, ADR-057).

These models define the wire contract for ``app.api.literature``. They were
extracted from that router so the data contract lives in one place, alongside
every other feature's schemas under ``app.schemas``. Moving them changes no
behavior: the field names, types, defaults, and validation are unchanged, and
the router imports these exact classes for its ``response_model`` wiring.
"""

from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AuthorPayload(BaseModel):
    given: str | None = None
    family: str | None = None
    orcid: str | None = None


class AssociationPayload(BaseModel):
    id: int
    scope_type: str
    scope_id: int | None
    scope_name: str | None
    parent_project_id: int | None = None
    parent_project_name: str | None = None
    added_by_user_id: int
    added_at: datetime


class PaperResponse(BaseModel):
    id: int
    title: str
    authors: list[AuthorPayload]
    publication_date: date_type | None
    journal: str | None
    doi: str | None
    pmid: str | None
    abstract: str | None
    provenance: str
    source: str | None
    added_by_user_id: int | None
    has_pdf: bool
    has_full_text: bool
    extraction_status: str
    extraction_error: str | None
    comment_count: int
    reading_status: str | None
    dismissed: bool
    in_library: bool
    associations: list[AssociationPayload]
    created_at: datetime
    updated_at: datetime


class RecommendationNotePayload(BaseModel):
    review_run_id: int
    experiment_id: int
    experiment_name: str | None = None
    project_name: str | None = None
    relevance_score: float
    relevance_bucket: str
    reasoning: str | None
    llm_provider: str
    llm_model: str
    created_at: datetime


class PaperListResponse(BaseModel):
    items: list[PaperResponse]
    total: int
    page: int
    page_size: int


class CreatePaperRequest(BaseModel):
    title: str
    authors: list[AuthorPayload] = Field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None
    journal: str | None = None
    publication_date: date_type | None = None
    abstract: str | None = None
    associations: list[dict] = Field(default_factory=list)


class UpdatePaperRequest(BaseModel):
    title: str | None = None
    authors: list[AuthorPayload] | None = None
    doi: str | None = None
    pmid: str | None = None
    journal: str | None = None
    publication_date: date_type | None = None
    abstract: str | None = None


class CommentPayload(BaseModel):
    id: int
    paper_id: int
    user_id: int
    user_name: str | None
    parent_id: int | None
    body: str | None
    deleted: bool
    deleted_by_user_id: int | None
    created_at: datetime
    updated_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentPayload]


class CreateCommentRequest(BaseModel):
    body: str
    parent_id: int | None = None


class UpdateCommentRequest(BaseModel):
    body: str


class ReadingStatusResponse(BaseModel):
    paper_id: int
    user_id: int
    status: str


class ReadingStatusRequest(BaseModel):
    status: Literal["unread", "reading", "read"]


class DismissalRequest(BaseModel):
    reason: str | None = None


class DismissalResponse(BaseModel):
    paper_id: int
    organization_id: int
    dismissed_by_user_id: int
    reason: str | None
    dismissed_at: datetime
    reversed_at: datetime | None
    reversed_by_user_id: int | None


class AssociationCreateRequest(BaseModel):
    scope_type: Literal["global", "project", "experiment"]
    scope_id: int | None = None


class CitationBulkRequest(BaseModel):
    paper_ids: list[int] | None = None
    scope_type: Literal["global", "project", "experiment"] | None = None
    scope_id: int | None = None
    format: Literal["bibtex", "ris"]


class LitReviewSettingsPayload(BaseModel):
    relevance_threshold: float
    auto_enabled: bool
    auto_cadence: str
    max_runs_per_tick: int
    # ISO 8601 timestamp of the next scheduled automated run (null when
    # automation is off or unscheduled). The UI prefills the first-run picker.
    next_run: str | None = None


class LitReviewSettingsUpdateRequest(BaseModel):
    # All optional so the relevance-threshold panel and the automation panel can
    # save independently; only provided fields are changed.
    relevance_threshold: float | None = None
    auto_enabled: bool | None = None
    auto_cadence: str | None = None
    max_runs_per_tick: int | None = None
    # ISO 8601 timestamp for when automation should first run; it then repeats
    # every cadence. A past/now value means it runs on the next tick.
    first_run: str | None = None


class BulkAddToLibraryRequest(BaseModel):
    paper_ids: list[int]


class BulkAddToLibraryResponse(BaseModel):
    added: list[int]
    not_found: list[int]


class BulkDismissRequest(BaseModel):
    paper_ids: list[int]
    reason: str | None = None


class BulkDismissResponse(BaseModel):
    dismissed: list[int]
    not_found: list[int]


class SourceConfigPayload(BaseModel):
    source: str
    enabled: bool
    has_api_key: bool
    rate_limit_override: int | None
    last_success_at: datetime | None
    last_status: str | None


class SourceConfigListResponse(BaseModel):
    items: list[SourceConfigPayload]


class SourceConfigUpdateRequest(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None
    rate_limit_override: int | None = None


class SourceTestResponse(BaseModel):
    success: bool
    message: str
    latency_ms: int


class SearchSubmitRequest(BaseModel):
    query: str
    sources: list[str] | None = None
    max_per_source: int = 50


class SearchPayload(BaseModel):
    id: int
    query_text: str
    sources: list[str]
    per_source_status: dict
    status: str
    result_count: int | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class SearchListResponse(BaseModel):
    items: list[SearchPayload]
    total: int


class LiteratureConfigPayload(BaseModel):
    scope_type: str
    scope_id: int | None
    abstracts_enabled: bool
    comments_enabled: bool
    full_text_enabled: bool
    max_tokens: int


class LiteratureConfigUpdateRequest(BaseModel):
    abstracts_enabled: bool | None = None
    comments_enabled: bool | None = None
    full_text_enabled: bool | None = None
    max_tokens: int | None = None


class LitReviewRunPayload(BaseModel):
    id: int
    experiment_id: int
    triggered_by_user_id: int
    status: str
    llm_provider: str
    llm_model: str
    expansion_queries_json: list[str] | None
    candidate_count: int | None
    recommendation_count: int | None
    max_recommendations: int
    score_threshold: float
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime


class LitReviewRunListResponse(BaseModel):
    items: list[LitReviewRunPayload]


class CreateLitReviewRunRequest(BaseModel):
    max_recommendations: int = 10
    # When omitted, the org's lit_review_relevance_threshold is used.
    score_threshold: float | None = None


class RecommendationPayload(BaseModel):
    id: int
    paper: PaperResponse
    experiment_id: int
    review_run_id: int
    relevance_score: float
    relevance_bucket: str
    reasoning: str | None
    status: str
    decided_by_user_id: int | None
    decided_at: datetime | None
    created_at: datetime


class RecommendationListResponse(BaseModel):
    items: list[RecommendationPayload]
    total: int
