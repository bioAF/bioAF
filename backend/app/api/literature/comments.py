"""Comment endpoints: threaded comments on Papers, with soft delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.literature import LiteraturePaperComment
from app.schemas.literature import CommentListResponse, CommentPayload, CreateCommentRequest, UpdateCommentRequest
from app.services import role_service
from app.services.literature import comment_service, paper_service
from app.services.literature.comment_service import CommentNotFound, CommentPermissionDenied
from app.services.literature.paper_service import PaperNotFound

router = APIRouter()


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
    rs = await session.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
    return {uid: (name or email) for (uid, name, email) in rs.all()}


async def _can_delete_any_comment(session: AsyncSession, current_user: dict) -> bool:
    role_id = int(current_user["role_id"])
    return await role_service.has_permission(session, role_id, "literature", "delete_any_comment")


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
    return CommentListResponse(items=[_serialize_comment(c, user_name=labels.get(c.user_id)) for c in rows])


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
    comment = await comment_service.create(
        session,
        paper_id=paper_id,
        user_id=int(current_user["sub"]),
        body=body.body,
        parent_id=body.parent_id,
    )
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
