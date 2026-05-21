"""Per-user reading status for a Paper."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import ALL_READING_STATUSES, LiteraturePaperReadingStatus
from app.services import audit_service


class InvalidReadingStatus(Exception):
    pass


async def set_status(
    session: AsyncSession,
    *,
    paper_id: int,
    user_id: int,
    status: str,
    api_key_id: int | None = None,
) -> LiteraturePaperReadingStatus:
    if status not in ALL_READING_STATUSES:
        raise InvalidReadingStatus(f"invalid reading status: {status}")
    result = await session.execute(
        select(LiteraturePaperReadingStatus).where(
            LiteraturePaperReadingStatus.paper_id == paper_id,
            LiteraturePaperReadingStatus.user_id == user_id,
        )
    )
    row = result.scalar_one_or_none()
    previous_status = row.status if row else None
    if row is None:
        row = LiteraturePaperReadingStatus(paper_id=paper_id, user_id=user_id, status=status)
        session.add(row)
    else:
        row.status = status
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_paper_reading_status",
        entity_id=paper_id,
        action="update",
        details={"status": status},
        previous_value={"status": previous_status},
    )
    return row


async def get_status(session: AsyncSession, paper_id: int, user_id: int) -> str | None:
    result = await session.execute(
        select(LiteraturePaperReadingStatus.status).where(
            LiteraturePaperReadingStatus.paper_id == paper_id,
            LiteraturePaperReadingStatus.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()
