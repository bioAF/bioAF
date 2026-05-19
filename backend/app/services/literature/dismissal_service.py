"""Org-wide Literature Dismissals (admin-reversible)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import LiteraturePaperDismissal
from app.services import audit_service


class DismissalNotFound(Exception):
    pass


async def dismiss(
    session: AsyncSession,
    *,
    paper_id: int,
    org_id: int,
    user_id: int,
    reason: str | None = None,
    api_key_id: int | None = None,
) -> LiteraturePaperDismissal:
    result = await session.execute(
        select(LiteraturePaperDismissal).where(LiteraturePaperDismissal.paper_id == paper_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.reversed_at is None:
            # Already actively dismissed: no-op.
            return existing
        # Re-dismiss: clear the reversal flags and update fields.
        existing.dismissed_by_user_id = user_id
        existing.reason = reason
        existing.dismissed_at = datetime.now(UTC)
        existing.reversed_at = None
        existing.reversed_by_user_id = None
        await session.flush()
        await audit_service.log_action(
            session,
            user_id=user_id,
            api_key_id=api_key_id,
            entity_type="literature_paper",
            entity_id=paper_id,
            action="dismiss",
            details={"reason": reason, "re_dismissed": True},
        )
        return existing

    dismissal = LiteraturePaperDismissal(
        paper_id=paper_id,
        organization_id=org_id,
        dismissed_by_user_id=user_id,
        reason=reason,
    )
    session.add(dismissal)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper_id,
        action="dismiss",
        details={"reason": reason},
    )
    return dismissal


async def reverse(
    session: AsyncSession,
    *,
    paper_id: int,
    user_id: int,
    api_key_id: int | None = None,
) -> LiteraturePaperDismissal:
    result = await session.execute(
        select(LiteraturePaperDismissal).where(LiteraturePaperDismissal.paper_id == paper_id)
    )
    dismissal = result.scalar_one_or_none()
    if dismissal is None or dismissal.reversed_at is not None:
        raise DismissalNotFound(f"no active dismissal for paper {paper_id}")
    dismissal.reversed_at = datetime.now(UTC)
    dismissal.reversed_by_user_id = user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper",
        entity_id=paper_id,
        action="dismiss_reverse",
    )
    return dismissal


async def is_dismissed(session: AsyncSession, paper_id: int) -> bool:
    result = await session.execute(
        select(LiteraturePaperDismissal).where(
            LiteraturePaperDismissal.paper_id == paper_id,
            LiteraturePaperDismissal.reversed_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None
