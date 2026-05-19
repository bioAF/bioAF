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
    ALL_PROVENANCES,
    ALL_READING_STATUSES,
    ALL_SCOPES,
    EXTERNAL_SOURCES,
    LiteratureAssociation,
    LiteraturePaper,
    LiteraturePaperComment,
    LiteraturePaperDismissal,
)
from app.models.user import User
from app.services import role_service
from app.services.literature import (
    association_service,
    citation_service,
    comment_service,
    dismissal_service,
    paper_service,
    reading_status_service,
    search_service,
    sources_config_service,
    storage,
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
    associations: list[AssociationPayload]
    created_at: datetime
    updated_at: datetime


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


async def _serialize_paper(
    session: AsyncSession, paper: LiteraturePaper, user_id: int
) -> PaperResponse:
    associations_rows = await association_service.list_for_paper(session, paper.id)
    associations = []
    for a in associations_rows:
        scope_name = await paper_service.scope_name_for(session, a.scope_type, a.scope_id)
        associations.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
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
        associations=associations,
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


def _serialize_comment(c: LiteraturePaperComment) -> CommentPayload:
    return CommentPayload(
        id=c.id,
        paper_id=c.paper_id,
        user_id=c.user_id,
        parent_id=c.parent_id,
        body=None if c.deleted_at is not None else c.body,
        deleted=c.deleted_at is not None,
        deleted_by_user_id=c.deleted_by_user_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


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
    provenance: str | None = Query(None),
    added_by_user_id: int | None = Query(None),
    has_full_text: bool | None = Query(None),
    source: str | None = Query(None),
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    show_dismissed: bool = Query(False),
    sort: str = Query("added"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if scope_type and scope_type not in ALL_SCOPES:
        raise HTTPException(400, "invalid scope_type")
    if provenance and provenance not in ALL_PROVENANCES:
        raise HTTPException(400, "invalid provenance")

    papers, total = await paper_service.list_papers(
        session,
        org_id=int(current_user["org_id"]),
        scope_type=scope_type,
        scope_id=scope_id,
        provenance=provenance,
        added_by_user_id=added_by_user_id,
        has_full_text=has_full_text,
        source=source,
        year_min=year_min,
        year_max=year_max,
        show_dismissed=show_dismissed,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    user_id = int(current_user["sub"])
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
    await upload_service.schedule_extraction(
        paper_id=paper.id, pdf_bytes=pdf_bytes, user_id=user_id
    )

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

    await upload_service.mark_extraction_pending(
        session, paper=paper, user_id=int(current_user["sub"])
    )
    await session.commit()
    await upload_service.schedule_extraction(
        paper_id=paper_id, pdf_bytes=pdf_bytes, user_id=int(current_user["sub"])
    )
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
        await paper_service.update_paper_metadata(
            session, paper=paper, user_id=user_id, fields=fields
        )
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
        dismissal = await dismissal_service.reverse(
            session, paper_id=paper_id, user_id=int(current_user["sub"])
        )
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
        items.append(
            AssociationPayload(
                id=a.id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                scope_name=scope_name,
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
    return AssociationPayload(
        id=assoc.id,
        scope_type=assoc.scope_type,
        scope_id=assoc.scope_id,
        scope_name=scope_name,
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
        await association_service.soft_remove(
            session, association_id=association_id, user_id=int(current_user["sub"])
        )
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
    return CommentListResponse(items=[_serialize_comment(c) for c in rows])


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
    return _serialize_comment(comment)


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
    return _serialize_comment(updated)


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
    row = await reading_status_service.set_status(
        session, paper_id=paper_id, user_id=user_id, status=body.status
    )
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
        row = await search_service.get_search(
            session, org_id=int(current_user["org_id"]), search_id=search_id
        )
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
        await search_service.get_search(
            session, org_id=int(current_user["org_id"]), search_id=search_id
        )
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
