"""Literature REST API (ADR-056, ADR-057).

Covers papers, comments, associations, reading status, dismissals, citation
export, and the sources/searches/recommendations endpoints. v1 is org-scoped
throughout: every endpoint filters on the caller's organization, so cross-org
access is impossible by construction.

This package splits what was a single ~1.5k-line router into one sub-router per
sub-domain. They are aggregated here under the original ``/api/literature``
prefix and ``literature`` tag, so the HTTP surface is unchanged. The Pydantic
schemas are re-exported for backward compatibility with any importer that read
them from this module.
"""

from fastapi import APIRouter

from app.schemas.literature import (
    AssociationCreateRequest,
    AssociationPayload,
    AuthorPayload,
    BulkAddToLibraryRequest,
    BulkAddToLibraryResponse,
    BulkDismissRequest,
    BulkDismissResponse,
    CitationBulkRequest,
    CommentListResponse,
    CommentPayload,
    CreateCommentRequest,
    CreateLitReviewRunRequest,
    CreatePaperRequest,
    DismissalRequest,
    DismissalResponse,
    LiteratureConfigPayload,
    LiteratureConfigUpdateRequest,
    LitReviewRunListResponse,
    LitReviewRunPayload,
    LitReviewSettingsPayload,
    LitReviewSettingsUpdateRequest,
    PaperListResponse,
    PaperResponse,
    ReadingStatusRequest,
    ReadingStatusResponse,
    RecommendationListResponse,
    RecommendationNotePayload,
    RecommendationPayload,
    SearchListResponse,
    SearchPayload,
    SearchSubmitRequest,
    SourceConfigListResponse,
    SourceConfigPayload,
    SourceConfigUpdateRequest,
    SourceTestResponse,
    UpdateCommentRequest,
    UpdatePaperRequest,
)

from . import (
    agent_review_config,
    associations,
    citations,
    comments,
    lit_review_runs,
    papers,
    reading_status,
    recommendations,
    searches,
    settings,
    sources,
)

router = APIRouter(prefix="/api/literature", tags=["literature"])
router.include_router(papers.router)
router.include_router(associations.router)
router.include_router(comments.router)
router.include_router(reading_status.router)
router.include_router(citations.router)
router.include_router(sources.router)
router.include_router(searches.router)
router.include_router(settings.router)
router.include_router(agent_review_config.router)
router.include_router(lit_review_runs.router)
router.include_router(recommendations.router)

__all__ = [
    "router",
    "AssociationCreateRequest",
    "AssociationPayload",
    "AuthorPayload",
    "BulkAddToLibraryRequest",
    "BulkAddToLibraryResponse",
    "BulkDismissRequest",
    "BulkDismissResponse",
    "CitationBulkRequest",
    "CommentListResponse",
    "CommentPayload",
    "CreateCommentRequest",
    "CreateLitReviewRunRequest",
    "CreatePaperRequest",
    "DismissalRequest",
    "DismissalResponse",
    "LiteratureConfigPayload",
    "LiteratureConfigUpdateRequest",
    "LitReviewRunListResponse",
    "LitReviewRunPayload",
    "LitReviewSettingsPayload",
    "LitReviewSettingsUpdateRequest",
    "PaperListResponse",
    "PaperResponse",
    "ReadingStatusRequest",
    "ReadingStatusResponse",
    "RecommendationListResponse",
    "RecommendationNotePayload",
    "RecommendationPayload",
    "SearchListResponse",
    "SearchPayload",
    "SearchSubmitRequest",
    "SourceConfigListResponse",
    "SourceConfigPayload",
    "SourceConfigUpdateRequest",
    "SourceTestResponse",
    "UpdateCommentRequest",
    "UpdatePaperRequest",
]
