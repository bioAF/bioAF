"""Literature REST API (ADR-056, ADR-057).

Covers papers, comments, associations, reading status, dismissals, citation
export, and the sources/searches/recommendations endpoints that later
implementation steps fill out. v1 is org-scoped throughout: every endpoint
filters on the caller's organization, so cross-org access is impossible by
construction.
"""

from __future__ import annotations

from datetime import date as date_type, datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.literature import (
    AgentReviewLiteratureConfig,
    ALL_PROVENANCES,
    ALL_SCOPES,
    EXTERNAL_SOURCES,
    LiteraturePaper,
    LiteraturePaperComment,
)
from app.services import role_service
from app.services.literature import (
    association_service,
    citation_service,
    comment_service,
    dismissal_service,
    lit_review_run_service,
    paper_service,
    reading_status_service,
    recommendation_service,
    search_service,
    sources_config_service,
    upload_service,
)
from app.services.literature.comment_service import (
    CommentNotFound,
    CommentPermissionDenied,
)
from app.services.literature.paper_service import DuplicatePaper, PaperNotFound

router = APIRouter(prefix="/api/literature", tags=["literature"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _serialize_paper(session: AsyncSession, paper: LiteraturePaper, user_id: int) -> PaperResponse:
    associations_rows = await association_service.list_for_paper(session, paper.id)
    associations = []
    for a in associations_rows:
        scope_name = await paper_service.scope_name_for(session, a.scope_type, a.scope_id)
        parent_pid, parent_pname = await paper_service.parent_project_for(
            session, a.scope_type, a.scope_id
        )
        associations.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
                parent_project_id=parent_pid,
                parent_project_name=parent_pname,
                added_by_user_id=a.added_by_user_id,
                added_at=a.added_at,
            )
        )

    return PaperResponse(
        id=paper.id,
        title=paper.title,
        authors=[AuthorPayload(**a) for a in (paper.authors_json or [])],
        publication_date=paper.publication_date,
        journal=paper.journal,
        doi=paper.doi,
        pmid=paper.pmid,
        abstract=paper.abstract,
        provenance=paper.provenance,
        source=paper.source,
        added_by_user_id=paper.added_by_user_id,
        has_pdf=bool(paper.gcs_pdf_uri),
        has_full_text=paper.has_full_text,
        extraction_status=paper.extraction_status,
        extraction_error=paper.extraction_error,
        comment_count=await paper_service.comment_count(session, paper.id),
        reading_status=await paper_service.reading_status_for(session, paper.id, user_id),
        dismissed=await paper_service.is_dismissed(session, paper.id),
        in_library=paper.in_library,
        associations=associations,
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


def _serialize_comment(c: LiteraturePaperComment, *, user_name: str | None = None) -> CommentPayload:
    return CommentPayload(
        id=c.id,
        paper_id=c.paper_id,
        user_id=c.user_id,
        user_name=user_name,
        parent_id=c.parent_id,
        body=None if c.deleted_at is not None else c.body,
        deleted=c.deleted_at is not None,
        deleted_by_user_id=c.deleted_by_user_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


async def _resolve_user_label(session: AsyncSession, user_id: int) -> str | None:
    from app.models.user import User

    rs = await session.execute(select(User.name, User.email).where(User.id == user_id))
    row = rs.first()
    if row is None:
        return None
    name, email = row
    return name or email


async def _user_labels(session: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    from app.models.user import User

    if not user_ids:
        return {}
    rs = await session.execute(
        select(User.id, User.name, User.email).where(User.id.in_(user_ids))
    )
    return {uid: (name or email) for (uid, name, email) in rs.all()}


async def _can_delete_any_comment(session: AsyncSession, current_user: dict) -> bool:
    role_id = int(current_user["role_id"])
    return await role_service.has_permission(session, role_id, "literature", "delete_any_comment")


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------


@router.get("/papers", response_model=PaperListResponse)
async def list_papers_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    scope_type: str | None = Query(None),
    scope_id: int | None = Query(None),
    project_id: int | None = Query(None),
    experiment_id: int | None = Query(None),
    provenance: str | None = Query(None),
    added_by_user_id: int | None = Query(None),
    has_full_text: bool | None = Query(None),
    source: str | None = Query(None),
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    in_library: bool | None = Query(True),
    include_active: bool = Query(True),
    include_dismissed: bool = Query(False),
    reading_status: list[str] | None = Query(None),
    sort: str = Query("added"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if scope_type and scope_type not in ALL_SCOPES:
        raise HTTPException(400, "invalid scope_type")
    if provenance and provenance not in ALL_PROVENANCES:
        raise HTTPException(400, "invalid provenance")

    user_id = int(current_user["sub"])
    reading_filter: tuple[str, ...] | None = None
    if reading_status is not None:
        reading_filter = tuple(reading_status)

    papers, total = await paper_service.list_papers(
        session,
        org_id=int(current_user["org_id"]),
        user_id=user_id,
        scope_type=scope_type,
        scope_id=scope_id,
        project_id=project_id,
        experiment_id=experiment_id,
        provenance=provenance,
        added_by_user_id=added_by_user_id,
        has_full_text=has_full_text,
        source=source,
        year_min=year_min,
        year_max=year_max,
        in_library=in_library,
        include_active=include_active,
        include_dismissed=include_dismissed,
        reading_statuses=reading_filter,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    items = [await _serialize_paper(session, p, user_id) for p in papers]
    return PaperListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/papers/upload", response_model=PaperResponse)
async def upload_paper_pdf_endpoint(
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    authors_json: str | None = Form(None),
    doi: str | None = Form(None),
    journal: str | None = Form(None),
    abstract: str | None = Form(None),
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    """Upload a PDF, extract metadata synchronously for the pre-fill form, and
    create the Paper row. Full-text extraction runs as an asyncio background
    task and updates the row when it completes."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF uploads are supported")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "uploaded file is empty")

    extracted = upload_service.synchronous_extract_basic(pdf_bytes)

    import json as _json

    parsed_authors: list[dict] | None = None
    if authors_json:
        try:
            parsed_authors = _json.loads(authors_json)
        except _json.JSONDecodeError:
            raise HTTPException(400, "authors_json must be valid JSON")

    resolved_title = title or extracted.get("title") or file.filename
    resolved_authors = parsed_authors if parsed_authors is not None else extracted.get("authors") or []
    resolved_doi = doi or extracted.get("doi")
    resolved_journal = journal or extracted.get("journal")
    resolved_abstract = abstract or extracted.get("abstract")
    resolved_pub_date = extracted.get("publication_date")

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.create_paper(
            session,
            org_id=org_id,
            user_id=user_id,
            title=resolved_title,
            authors=resolved_authors,
            doi=resolved_doi,
            journal=resolved_journal,
            publication_date=resolved_pub_date,
            abstract=resolved_abstract,
            provenance="user_upload",
            source="upload",
        )
    except DuplicatePaper as e:
        existing = await paper_service.get_paper(session, org_id, e.existing_paper_id)
        response.status_code = 200
        return await _serialize_paper(session, existing, user_id)

    uri = await upload_service.upload_pdf_to_gcs(session, paper_id=paper.id, pdf_bytes=pdf_bytes)
    if uri:
        paper.gcs_pdf_uri = uri
    await upload_service.mark_extraction_pending(session, paper=paper, user_id=user_id)
    await session.commit()

    # Fire and forget the heavy extraction (full text + persistence).
    await upload_service.schedule_extraction(paper_id=paper.id, pdf_bytes=pdf_bytes, user_id=user_id)

    response.status_code = 201
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.post("/papers/{paper_id}/extract", response_model=PaperResponse)
async def re_extract_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if not paper.gcs_pdf_uri:
        raise HTTPException(400, "paper has no PDF to extract")

    pdf_bytes = await _download_pdf_bytes(session, paper.gcs_pdf_uri)
    if pdf_bytes is None:
        raise HTTPException(500, "could not download paper PDF")

    await upload_service.mark_extraction_pending(session, paper=paper, user_id=int(current_user["sub"]))
    await session.commit()
    await upload_service.schedule_extraction(paper_id=paper_id, pdf_bytes=pdf_bytes, user_id=int(current_user["sub"]))
    await session.refresh(paper)
    return await _serialize_paper(session, paper, int(current_user["sub"]))


async def _download_pdf_bytes(session: AsyncSession, gcs_uri: str) -> bytes | None:
    import asyncio

    from app.services.gcs_storage import GcsStorageService

    try:
        credentials = await GcsStorageService.get_credentials(session)
        from google.cloud import storage as gcs

        loop = asyncio.get_running_loop()

        def _download() -> bytes:
            client = gcs.Client(credentials=credentials)
            parts = gcs_uri.replace("gs://", "").split("/", 1)
            bucket = client.bucket(parts[0])
            return bucket.blob(parts[1]).download_as_bytes()

        return await loop.run_in_executor(None, _download)
    except Exception:
        return None


@router.get("/papers/{paper_id}/pdf")
async def download_pdf_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if not paper.gcs_pdf_uri:
        raise HTTPException(404, "no PDF uploaded for this paper")
    data = await _download_pdf_bytes(session, paper.gcs_pdf_uri)
    if data is None:
        raise HTTPException(500, "could not fetch PDF")

    def stream():
        yield data

    return StreamingResponse(stream(), media_type="application/pdf")


@router.post("/papers", response_model=PaperResponse)
async def create_paper_endpoint(
    body: CreatePaperRequest,
    response: Response,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    authors_dicts = [a.model_dump(exclude_none=True) for a in body.authors]
    try:
        paper = await paper_service.create_paper(
            session,
            org_id=org_id,
            user_id=user_id,
            title=body.title,
            authors=authors_dicts,
            doi=body.doi,
            pmid=body.pmid,
            journal=body.journal,
            publication_date=body.publication_date,
            abstract=body.abstract,
        )
    except DuplicatePaper as e:
        existing = await paper_service.get_paper(session, org_id, e.existing_paper_id)
        response.status_code = 200
        return await _serialize_paper(session, existing, user_id)
    response.status_code = 201
    for assoc in body.associations:
        if "scope_type" not in assoc:
            continue
        await association_service.get_or_create(
            session,
            paper=paper,
            user_id=user_id,
            scope_type=assoc["scope_type"],
            scope_id=assoc.get("scope_id"),
        )
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.get("/papers/{paper_id}", response_model=PaperResponse)
async def get_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        paper = await paper_service.get_paper(session, int(current_user["org_id"]), paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    return await _serialize_paper(session, paper, int(current_user["sub"]))


@router.get(
    "/papers/{paper_id}/recommendation-notes",
    response_model=list[RecommendationNotePayload],
)
async def list_recommendation_notes_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        await paper_service.get_paper(session, int(current_user["org_id"]), paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    notes = await paper_service.list_recommendation_notes(session, paper_id)
    return [RecommendationNotePayload(**n) for n in notes]


@router.post("/papers/{paper_id}/add-to-library", response_model=PaperResponse)
async def add_paper_to_library_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    await paper_service.add_to_library(session, paper=paper, user_id=user_id)
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


class LitReviewSettingsPayload(BaseModel):
    relevance_threshold: float


class LitReviewSettingsUpdateRequest(BaseModel):
    relevance_threshold: float


class BulkAddToLibraryRequest(BaseModel):
    paper_ids: list[int]


class BulkAddToLibraryResponse(BaseModel):
    added: list[int]
    not_found: list[int]


@router.get("/settings/lit-review", response_model=LitReviewSettingsPayload)
async def get_lit_review_settings_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization

    org_id = int(current_user["org_id"])
    rs = await session.execute(
        select(Organization.lit_review_relevance_threshold).where(Organization.id == org_id)
    )
    value = rs.scalar_one_or_none()
    if value is None:
        value = 0.65
    return LitReviewSettingsPayload(relevance_threshold=float(value))


@router.put("/settings/lit-review", response_model=LitReviewSettingsPayload)
async def update_lit_review_settings_endpoint(
    body: LitReviewSettingsUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    from app.models.organization import Organization
    from app.services import audit_service

    threshold = body.relevance_threshold
    if not (0.0 <= threshold <= 1.0):
        raise HTTPException(400, "relevance_threshold must be between 0.0 and 1.0")

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    rs = await session.execute(select(Organization).where(Organization.id == org_id))
    org = rs.scalar_one()
    previous = org.lit_review_relevance_threshold
    org.lit_review_relevance_threshold = threshold
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="organization",
        entity_id=org_id,
        action="update_lit_review_threshold",
        details={"relevance_threshold": threshold},
        previous_value={"relevance_threshold": previous},
    )
    await session.commit()
    return LitReviewSettingsPayload(relevance_threshold=threshold)


@router.post("/papers/bulk-add-to-library", response_model=BulkAddToLibraryResponse)
async def bulk_add_to_library_endpoint(
    body: BulkAddToLibraryRequest,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    added: list[int] = []
    not_found: list[int] = []
    for pid in body.paper_ids:
        try:
            paper = await paper_service.get_paper(session, org_id, pid)
        except PaperNotFound:
            not_found.append(pid)
            continue
        await paper_service.add_to_library(session, paper=paper, user_id=user_id)
        added.append(pid)
    await session.commit()
    return BulkAddToLibraryResponse(added=added, not_found=not_found)


@router.patch("/papers/{paper_id}", response_model=PaperResponse)
async def update_paper_endpoint(
    paper_id: int,
    body: UpdatePaperRequest,
    current_user: dict = require_permission("literature", "upload"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    fields = body.model_dump(exclude_unset=True)
    if "authors" in fields and fields["authors"] is not None:
        fields["authors"] = [a if isinstance(a, dict) else a.model_dump(exclude_none=True) for a in fields["authors"]]
    try:
        await paper_service.update_paper_metadata(session, paper=paper, user_id=user_id, fields=fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    await session.refresh(paper)
    return await _serialize_paper(session, paper, user_id)


@router.delete("/papers/{paper_id}", status_code=204)
async def delete_paper_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "delete_paper"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    await paper_service.delete_paper(session, paper=paper, user_id=int(current_user["sub"]))
    await session.commit()


@router.post("/papers/{paper_id}/dismiss", response_model=DismissalResponse)
async def dismiss_paper_endpoint(
    paper_id: int,
    body: DismissalRequest,
    current_user: dict = require_permission("literature", "dismiss"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    dismissal = await dismissal_service.dismiss(
        session,
        paper_id=paper_id,
        org_id=org_id,
        user_id=int(current_user["sub"]),
        reason=body.reason,
    )
    await session.commit()
    return DismissalResponse(
        paper_id=dismissal.paper_id,
        organization_id=dismissal.organization_id,
        dismissed_by_user_id=dismissal.dismissed_by_user_id,
        reason=dismissal.reason,
        dismissed_at=dismissal.dismissed_at,
        reversed_at=dismissal.reversed_at,
        reversed_by_user_id=dismissal.reversed_by_user_id,
    )


@router.post("/papers/{paper_id}/dismiss/reverse", response_model=DismissalResponse)
async def reverse_dismissal_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "reverse_dismiss"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        dismissal = await dismissal_service.reverse(session, paper_id=paper_id, user_id=int(current_user["sub"]))
    except Exception:
        raise HTTPException(404, "no active dismissal")
    await session.commit()
    return DismissalResponse(
        paper_id=dismissal.paper_id,
        organization_id=dismissal.organization_id,
        dismissed_by_user_id=dismissal.dismissed_by_user_id,
        reason=dismissal.reason,
        dismissed_at=dismissal.dismissed_at,
        reversed_at=dismissal.reversed_at,
        reversed_by_user_id=dismissal.reversed_by_user_id,
    )


# ---------------------------------------------------------------------------
# Associations
# ---------------------------------------------------------------------------


@router.get("/papers/{paper_id}/associations", response_model=list[AssociationPayload])
async def list_associations_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    items = []
    for a in await association_service.list_for_paper(session, paper_id):
        scope_name = await paper_service.scope_name_for(session, a.scope_type, a.scope_id)
        parent_pid, parent_pname = await paper_service.parent_project_for(
            session, a.scope_type, a.scope_id
        )
        items.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
                parent_project_id=parent_pid,
                parent_project_name=parent_pname,
                added_by_user_id=a.added_by_user_id,
                added_at=a.added_at,
            )
        )
    return items


@router.post("/papers/{paper_id}/associations", response_model=AssociationPayload)
async def create_association_endpoint(
    paper_id: int,
    body: AssociationCreateRequest,
    current_user: dict = require_permission("literature", "associate"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        assoc = await association_service.get_or_create(
            session,
            paper=paper,
            user_id=int(current_user["sub"]),
            scope_type=body.scope_type,
            scope_id=body.scope_id,
        )
    except association_service.InvalidScope as e:
        raise HTTPException(400, str(e))
    await session.commit()
    scope_name = await paper_service.scope_name_for(session, assoc.scope_type, assoc.scope_id)
    parent_pid, parent_pname = await paper_service.parent_project_for(
        session, assoc.scope_type, assoc.scope_id
    )
    return AssociationPayload(
        id=assoc.id,
        scope_type=assoc.scope_type,
        scope_id=assoc.scope_id,
        scope_name=scope_name,
        parent_project_id=parent_pid,
        parent_project_name=parent_pname,
        added_by_user_id=assoc.added_by_user_id,
        added_at=assoc.added_at,
    )


@router.delete("/papers/{paper_id}/associations/{association_id}", status_code=204)
async def delete_association_endpoint(
    paper_id: int,
    association_id: int,
    current_user: dict = require_permission("literature", "associate"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        await association_service.soft_remove(session, association_id=association_id, user_id=int(current_user["sub"]))
    except association_service.AssociationNotFound:
        raise HTTPException(404, "association not found")
    await session.commit()


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@router.get("/papers/{paper_id}/comments", response_model=CommentListResponse)
async def list_comments_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    rows = await comment_service.list_for_paper(session, paper_id)
    labels = await _user_labels(session, {c.user_id for c in rows})
    return CommentListResponse(
        items=[_serialize_comment(c, user_name=labels.get(c.user_id)) for c in rows]
    )


@router.post("/papers/{paper_id}/comments", response_model=CommentPayload, status_code=201)
async def create_comment_endpoint(
    paper_id: int,
    body: CreateCommentRequest,
    current_user: dict = require_permission("literature", "comment"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    try:
        comment = await comment_service.create(
            session,
            paper_id=paper_id,
            user_id=int(current_user["sub"]),
            body=body.body,
            parent_id=body.parent_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    user_name = await _resolve_user_label(session, comment.user_id)
    return _serialize_comment(comment, user_name=user_name)


@router.patch("/comments/{comment_id}", response_model=CommentPayload)
async def update_comment_endpoint(
    comment_id: int,
    body: UpdateCommentRequest,
    current_user: dict = require_permission("literature", "comment"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        comment = await comment_service.get(session, comment_id)
        # cross-org isolation: comment belongs to a paper in this org
        await paper_service.get_paper(session, org_id, comment.paper_id)
    except (CommentNotFound, PaperNotFound):
        raise HTTPException(404, "comment not found")
    can_edit_any = await _can_delete_any_comment(session, current_user)
    try:
        updated = await comment_service.update(
            session,
            comment_id=comment_id,
            user_id=int(current_user["sub"]),
            body=body.body,
            can_edit_any=can_edit_any,
        )
    except CommentPermissionDenied:
        raise HTTPException(403, "cannot edit this comment")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    user_name = await _resolve_user_label(session, updated.user_id)
    return _serialize_comment(updated, user_name=user_name)


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment_endpoint(
    comment_id: int,
    current_user: dict = require_permission("literature", "delete_own_comment"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        comment = await comment_service.get(session, comment_id)
        await paper_service.get_paper(session, org_id, comment.paper_id)
    except (CommentNotFound, PaperNotFound):
        raise HTTPException(404, "comment not found")
    can_delete_any = await _can_delete_any_comment(session, current_user)
    try:
        await comment_service.soft_delete(
            session,
            comment_id=comment_id,
            user_id=int(current_user["sub"]),
            can_delete_any=can_delete_any,
        )
    except CommentPermissionDenied:
        raise HTTPException(403, "cannot delete this comment")
    await session.commit()


# ---------------------------------------------------------------------------
# Reading status
# ---------------------------------------------------------------------------


@router.get("/papers/{paper_id}/reading-status", response_model=ReadingStatusResponse)
async def get_reading_status_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    user_id = int(current_user["sub"])
    status = await reading_status_service.get_status(session, paper_id, user_id) or "unread"
    return ReadingStatusResponse(paper_id=paper_id, user_id=user_id, status=status)


@router.put("/papers/{paper_id}/reading-status", response_model=ReadingStatusResponse)
async def set_reading_status_endpoint(
    paper_id: int,
    body: ReadingStatusRequest,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    user_id = int(current_user["sub"])
    row = await reading_status_service.set_status(session, paper_id=paper_id, user_id=user_id, status=body.status)
    await session.commit()
    return ReadingStatusResponse(paper_id=paper_id, user_id=user_id, status=row.status)


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


@router.get("/papers/{paper_id}/citation", response_class=PlainTextResponse)
async def single_citation_endpoint(
    paper_id: int,
    format: Literal["bibtex", "ris"] = Query("bibtex"),
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        paper = await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    if format == "bibtex":
        return citation_service.to_bibtex(paper)
    return citation_service.to_ris(paper)


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


@router.get("/sources", response_model=SourceConfigListResponse)
async def list_sources_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    rows = await sources_config_service.list_for_org(session, int(current_user["org_id"]))
    items = [
        SourceConfigPayload(
            source=row.source,
            enabled=row.enabled,
            has_api_key=bool(row.api_key),
            rate_limit_override=row.rate_limit_override,
            last_success_at=row.last_success_at,
            last_status=row.last_status,
        )
        for row in rows
    ]
    return SourceConfigListResponse(items=items)


@router.patch("/sources/{source}", response_model=SourceConfigPayload)
async def update_source_endpoint(
    source: str,
    body: SourceConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if source not in EXTERNAL_SOURCES:
        raise HTTPException(404, "unknown source")
    try:
        row = await sources_config_service.update(
            session,
            org_id=int(current_user["org_id"]),
            source=source,
            user_id=int(current_user["sub"]),
            enabled=body.enabled,
            api_key=body.api_key,
            rate_limit_override=body.rate_limit_override,
        )
    except sources_config_service.UnknownSource:
        raise HTTPException(404, "unknown source")
    await session.commit()
    return SourceConfigPayload(
        source=row.source,
        enabled=row.enabled,
        has_api_key=bool(row.api_key),
        rate_limit_override=row.rate_limit_override,
        last_success_at=row.last_success_at,
        last_status=row.last_status,
    )


@router.post("/sources/{source}/test", response_model=SourceTestResponse)
async def test_source_endpoint(
    source: str,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if source not in EXTERNAL_SOURCES:
        raise HTTPException(404, "unknown source")
    row = await sources_config_service.get_or_create(session, int(current_user["org_id"]), source)
    result = await sources_config_service.test_connection(source, row.api_key)
    return SourceTestResponse(**result)


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


def _serialize_search(s) -> SearchPayload:
    return SearchPayload(
        id=s.id,
        query_text=s.query_text,
        sources=list(s.sources_json or []),
        per_source_status=dict(s.per_source_status or {}),
        status=s.status,
        result_count=s.result_count,
        error_message=s.error_message,
        started_at=s.started_at,
        completed_at=s.completed_at,
        created_at=s.created_at,
    )


@router.post("/searches", response_model=SearchPayload, status_code=201)
async def submit_search_endpoint(
    body: SearchSubmitRequest,
    current_user: dict = require_permission("literature", "run_search"),
    session: AsyncSession = Depends(get_session),
):
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query must not be empty")
    try:
        row = await search_service.create_search(
            session,
            org_id=int(current_user["org_id"]),
            user_id=int(current_user["sub"]),
            query=body.query.strip(),
            sources=body.sources,
            max_per_source=body.max_per_source,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    await session.commit()
    await search_service.schedule_search_run(
        search_id=row.id, user_id=int(current_user["sub"]), max_per_source=body.max_per_source
    )
    return _serialize_search(row)


@router.get("/searches", response_model=SearchListResponse)
async def list_searches_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    user_id = int(current_user["sub"])
    # Admin / comp_bio see all searches; everyone else sees only their own.
    can_see_all = await role_service.has_permission(
        session, int(current_user["role_id"]), "literature", "configure_sources"
    )
    rows, total = await search_service.list_searches(
        session,
        org_id=int(current_user["org_id"]),
        user_id=None if can_see_all else user_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return SearchListResponse(items=[_serialize_search(r) for r in rows], total=total)


@router.get("/searches/{search_id}", response_model=SearchPayload)
async def get_search_endpoint(
    search_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await search_service.get_search(session, org_id=int(current_user["org_id"]), search_id=search_id)
    except search_service.SearchNotFound:
        raise HTTPException(404, "search not found")
    return _serialize_search(row)


@router.get("/searches/{search_id}/results", response_model=PaperListResponse)
async def get_search_results_endpoint(
    search_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        await search_service.get_search(session, org_id=int(current_user["org_id"]), search_id=search_id)
    except search_service.SearchNotFound:
        raise HTTPException(404, "search not found")
    pairs = await search_service.list_search_results(session, search_id=search_id)
    seen: set[int] = set()
    user_id = int(current_user["sub"])
    items: list[PaperResponse] = []
    for _, paper in pairs:
        if paper.id in seen:
            continue
        seen.add(paper.id)
        items.append(await _serialize_paper(session, paper, user_id))
    return PaperListResponse(items=items, total=len(items), page=1, page_size=len(items) or 1)


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


async def _get_literature_config(
    session: AsyncSession, *, org_id: int, scope_type: str, scope_id: int | None
) -> AgentReviewLiteratureConfig | None:
    query = select(AgentReviewLiteratureConfig).where(
        AgentReviewLiteratureConfig.organization_id == org_id,
        AgentReviewLiteratureConfig.scope_type == scope_type,
    )
    if scope_id is None:
        query = query.where(AgentReviewLiteratureConfig.scope_id.is_(None))
    else:
        query = query.where(AgentReviewLiteratureConfig.scope_id == scope_id)
    return (await session.execute(query)).scalar_one_or_none()


def _serialize_literature_config(
    row: AgentReviewLiteratureConfig | None, *, scope_type: str, scope_id: int | None
) -> LiteratureConfigPayload:
    if row is None:
        return LiteratureConfigPayload(
            scope_type=scope_type,
            scope_id=scope_id,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=False,
            max_tokens=100_000,
        )
    return LiteratureConfigPayload(
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        abstracts_enabled=row.abstracts_enabled,
        comments_enabled=row.comments_enabled,
        full_text_enabled=row.full_text_enabled,
        max_tokens=row.max_tokens,
    )


@router.get("/agent-review-config", response_model=LiteratureConfigPayload)
async def get_org_literature_config_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    row = await _get_literature_config(session, org_id=org_id, scope_type="org", scope_id=None)
    return _serialize_literature_config(row, scope_type="org", scope_id=None)


@router.put("/agent-review-config", response_model=LiteratureConfigPayload)
async def update_org_literature_config_endpoint(
    body: LiteratureConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    return await _upsert_literature_config(
        session=session,
        current_user=current_user,
        scope_type="org",
        scope_id=None,
        body=body,
    )


@router.get("/agent-review-config/{scope_type}/{scope_id}", response_model=LiteratureConfigPayload)
async def get_scoped_literature_config_endpoint(
    scope_type: str,
    scope_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    if scope_type not in {"experiment", "project"}:
        raise HTTPException(400, "scope_type must be experiment or project")
    org_id = int(current_user["org_id"])
    row = await _get_literature_config(session, org_id=org_id, scope_type=scope_type, scope_id=scope_id)
    return _serialize_literature_config(row, scope_type=scope_type, scope_id=scope_id)


@router.put("/agent-review-config/{scope_type}/{scope_id}", response_model=LiteratureConfigPayload)
async def update_scoped_literature_config_endpoint(
    scope_type: str,
    scope_id: int,
    body: LiteratureConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if scope_type not in {"experiment", "project"}:
        raise HTTPException(400, "scope_type must be experiment or project")
    return await _upsert_literature_config(
        session=session,
        current_user=current_user,
        scope_type=scope_type,
        scope_id=scope_id,
        body=body,
    )


async def _upsert_literature_config(
    *,
    session: AsyncSession,
    current_user: dict,
    scope_type: str,
    scope_id: int | None,
    body: LiteratureConfigUpdateRequest,
) -> LiteratureConfigPayload:
    from app.services import audit_service

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    row = await _get_literature_config(session, org_id=org_id, scope_type=scope_type, scope_id=scope_id)
    previous = None
    if row is None:
        row = AgentReviewLiteratureConfig(
            organization_id=org_id,
            scope_type=scope_type,
            scope_id=scope_id,
            abstracts_enabled=True,
            comments_enabled=True,
            full_text_enabled=False,
            max_tokens=100_000,
            updated_by_user_id=user_id,
        )
        session.add(row)
    else:
        previous = {
            "abstracts_enabled": row.abstracts_enabled,
            "comments_enabled": row.comments_enabled,
            "full_text_enabled": row.full_text_enabled,
            "max_tokens": row.max_tokens,
        }
    if body.abstracts_enabled is not None:
        row.abstracts_enabled = body.abstracts_enabled
    if body.comments_enabled is not None:
        row.comments_enabled = body.comments_enabled
    if body.full_text_enabled is not None:
        row.full_text_enabled = body.full_text_enabled
    if body.max_tokens is not None:
        if body.max_tokens < 1000 or body.max_tokens > 1_000_000:
            raise HTTPException(400, "max_tokens must be between 1000 and 1000000")
        row.max_tokens = body.max_tokens
    row.updated_by_user_id = user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        entity_type="agent_review_literature_config",
        entity_id=row.id,
        action="update",
        details={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "abstracts_enabled": row.abstracts_enabled,
            "comments_enabled": row.comments_enabled,
            "full_text_enabled": row.full_text_enabled,
            "max_tokens": row.max_tokens,
        },
        previous_value=previous,
    )
    await session.commit()
    return _serialize_literature_config(row, scope_type=scope_type, scope_id=scope_id)


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


def _serialize_run(r) -> LitReviewRunPayload:
    return LitReviewRunPayload(
        id=r.id,
        experiment_id=r.experiment_id,
        triggered_by_user_id=r.triggered_by_user_id,
        status=r.status,
        llm_provider=r.llm_provider,
        llm_model=r.llm_model,
        expansion_queries_json=r.expansion_queries_json,
        candidate_count=r.candidate_count,
        recommendation_count=r.recommendation_count,
        max_recommendations=r.max_recommendations,
        score_threshold=r.score_threshold,
        started_at=r.started_at,
        completed_at=r.completed_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


@router.post(
    "/experiments/{experiment_id}/lit-review-runs",
    response_model=LitReviewRunPayload,
    status_code=201,
)
async def create_lit_review_run_endpoint(
    experiment_id: int,
    body: CreateLitReviewRunRequest = Body(default_factory=CreateLitReviewRunRequest),
    current_user: dict = require_permission("literature", "run_lit_review"),
    session: AsyncSession = Depends(get_session),
):
    try:
        run = await lit_review_run_service.create_run(
            session,
            org_id=int(current_user["org_id"]),
            experiment_id=experiment_id,
            triggered_by_user_id=int(current_user["sub"]),
            max_recommendations=body.max_recommendations,
            score_threshold=body.score_threshold,
        )
    except lit_review_run_service.NoActiveLlmProvider:
        raise HTTPException(409, "no_active_llm_provider")
    except lit_review_run_service.ReviewRunFailed as e:
        raise HTTPException(400, str(e))
    await session.commit()
    await lit_review_run_service.schedule_run(run_id=run.id)
    return _serialize_run(run)


@router.get(
    "/experiments/{experiment_id}/lit-review-runs",
    response_model=LitReviewRunListResponse,
)
async def list_lit_review_runs_endpoint(
    experiment_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    rows = await lit_review_run_service.list_runs_for_experiment(
        session, org_id=int(current_user["org_id"]), experiment_id=experiment_id
    )
    return LitReviewRunListResponse(items=[_serialize_run(r) for r in rows])


@router.get("/lit-review-runs/{run_id}", response_model=LitReviewRunPayload)
async def get_lit_review_run_endpoint(
    run_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    row = await lit_review_run_service.get_run(session, org_id=int(current_user["org_id"]), run_id=run_id)
    if row is None:
        raise HTTPException(404, "lit review run not found")
    return _serialize_run(row)


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


async def _serialize_recommendation(session: AsyncSession, rec, user_id: int) -> RecommendationPayload:
    paper = await paper_service.get_paper(session, rec.organization_id, rec.paper_id)
    return RecommendationPayload(
        id=rec.id,
        paper=await _serialize_paper(session, paper, user_id),
        experiment_id=rec.experiment_id,
        review_run_id=rec.review_run_id,
        relevance_score=rec.relevance_score,
        relevance_bucket=rec.relevance_bucket,
        reasoning=rec.reasoning,
        status=rec.status,
        decided_by_user_id=rec.decided_by_user_id,
        decided_at=rec.decided_at,
        created_at=rec.created_at,
    )


@router.get("/recommendations", response_model=RecommendationListResponse)
async def list_recommendations_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    experiment_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    rows, total = await recommendation_service.list_for_org(
        session,
        org_id=int(current_user["org_id"]),
        experiment_id=experiment_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    user_id = int(current_user["sub"])
    items = [await _serialize_recommendation(session, r, user_id) for r in rows]
    return RecommendationListResponse(items=items, total=total)


@router.post("/recommendations/{recommendation_id}/accept", response_model=RecommendationPayload)
async def accept_recommendation_endpoint(
    recommendation_id: int,
    current_user: dict = require_permission("literature", "run_lit_review"),
    session: AsyncSession = Depends(get_session),
):
    try:
        rec = await recommendation_service.accept(
            session,
            org_id=int(current_user["org_id"]),
            recommendation_id=recommendation_id,
            user_id=int(current_user["sub"]),
        )
    except recommendation_service.RecommendationNotFound:
        raise HTTPException(404, "recommendation not found")
    except recommendation_service.RecommendationAlreadyDecided as e:
        raise HTTPException(409, str(e))
    await session.commit()
    return await _serialize_recommendation(session, rec, int(current_user["sub"]))


@router.post("/recommendations/{recommendation_id}/dismiss", response_model=RecommendationPayload)
async def dismiss_recommendation_endpoint(
    recommendation_id: int,
    current_user: dict = require_permission("literature", "dismiss"),
    session: AsyncSession = Depends(get_session),
):
    try:
        rec = await recommendation_service.dismiss(
            session,
            org_id=int(current_user["org_id"]),
            recommendation_id=recommendation_id,
            user_id=int(current_user["sub"]),
        )
    except recommendation_service.RecommendationNotFound:
        raise HTTPException(404, "recommendation not found")
    except recommendation_service.RecommendationAlreadyDecided as e:
        raise HTTPException(409, str(e))
    await session.commit()
    return await _serialize_recommendation(session, rec, int(current_user["sub"]))


@router.post("/citations/bulk", response_class=PlainTextResponse)
async def bulk_citation_endpoint(
    body: CitationBulkRequest,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    papers: list[LiteraturePaper] = []
    if body.paper_ids:
        for pid in body.paper_ids:
            try:
                papers.append(await paper_service.get_paper(session, org_id, pid))
            except PaperNotFound:
                continue
    elif body.scope_type:
        rows, _ = await paper_service.list_papers(
            session,
            org_id=org_id,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            page=1,
            page_size=200,
        )
        papers.extend(rows)
    return citation_service.bulk_export(papers, body.format)
