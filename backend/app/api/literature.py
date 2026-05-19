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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
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
