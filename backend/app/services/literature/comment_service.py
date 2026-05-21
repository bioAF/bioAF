"""Threaded comments on Papers. One entity with parent_id self-reference and
soft delete via deleted_at."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import LiteraturePaperComment
from app.services import audit_service


class CommentNotFound(Exception):
    pass


class CommentPermissionDenied(Exception):
    pass


async def create(
    session: AsyncSession,
    *,
    paper_id: int,
    user_id: int,
    body: str,
    parent_id: int | None = None,
    api_key_id: int | None = None,
) -> LiteraturePaperComment:
    if not body or not body.strip():
        raise ValueError("comment body must be non-empty")
    if parent_id is not None:
        parent = await get(session, parent_id)
        if parent.paper_id != paper_id:
            raise ValueError("parent comment belongs to a different paper")
    comment = LiteraturePaperComment(paper_id=paper_id, user_id=user_id, body=body.strip(), parent_id=parent_id)
    session.add(comment)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper_comment",
        entity_id=comment.id,
        action="create",
        details={"paper_id": paper_id, "parent_id": parent_id},
    )
    return comment


async def get(session: AsyncSession, comment_id: int) -> LiteraturePaperComment:
    result = await session.execute(select(LiteraturePaperComment).where(LiteraturePaperComment.id == comment_id))
    comment = result.scalar_one_or_none()
    if comment is None:
        raise CommentNotFound(f"comment {comment_id} not found")
    return comment


async def list_for_paper(
    session: AsyncSession, paper_id: int, *, include_deleted: bool = True
) -> list[LiteraturePaperComment]:
    """Return all comments on a paper ordered by (parent_id NULLS FIRST, created_at).

    Deletions are included by default (soft-deleted bodies render as placeholders
    in the UI so the conversation shape is preserved)."""
    query = select(LiteraturePaperComment).where(LiteraturePaperComment.paper_id == paper_id)
    if not include_deleted:
        query = query.where(LiteraturePaperComment.deleted_at.is_(None))
    query = query.order_by(
        LiteraturePaperComment.parent_id.nullsfirst(),
        LiteraturePaperComment.created_at,
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def update(
    session: AsyncSession,
    *,
    comment_id: int,
    user_id: int,
    body: str,
    can_edit_any: bool = False,
    api_key_id: int | None = None,
) -> LiteraturePaperComment:
    comment = await get(session, comment_id)
    if comment.user_id != user_id and not can_edit_any:
        raise CommentPermissionDenied(f"cannot edit comment {comment_id}")
    if comment.deleted_at is not None:
        raise ValueError("cannot edit a deleted comment")
    if not body or not body.strip():
        raise ValueError("comment body must be non-empty")
    previous_body = comment.body
    comment.body = body.strip()
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper_comment",
        entity_id=comment.id,
        action="update",
        previous_value={"body": previous_body},
    )
    return comment


async def soft_delete(
    session: AsyncSession,
    *,
    comment_id: int,
    user_id: int,
    can_delete_any: bool = False,
    api_key_id: int | None = None,
) -> LiteraturePaperComment:
    comment = await get(session, comment_id)
    if comment.deleted_at is not None:
        return comment
    if comment.user_id != user_id and not can_delete_any:
        raise CommentPermissionDenied(f"cannot delete comment {comment_id}")
    comment.deleted_at = datetime.now(UTC)
    comment.deleted_by_user_id = user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper_comment",
        entity_id=comment.id,
        action="delete",
        details={"by_owner": comment.user_id == user_id},
    )
    return comment
