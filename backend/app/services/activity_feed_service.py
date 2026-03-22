"""Activity feed service for tracking platform events."""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.activity_feed import ActivityFeedEntry

logger = logging.getLogger("bioaf.activity_feed_service")


class ActivityFeedService:
    @staticmethod
    async def add_event(
        session: AsyncSession,
        org_id: int,
        user_id: int | None,
        event_type: str,
        summary: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
        metadata: dict | None = None,
    ) -> ActivityFeedEntry:
        entry = ActivityFeedEntry(
            organization_id=org_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            metadata_json=metadata or {},
        )
        session.add(entry)
        await session.flush()
        return entry

    @staticmethod
    async def list_events(
        session: AsyncSession,
        org_id: int,
        page: int = 1,
        page_size: int = 50,
        event_type: str | None = None,
        entity_type: str | None = None,
        user_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[list[ActivityFeedEntry], int]:
        base = select(ActivityFeedEntry).where(ActivityFeedEntry.organization_id == org_id)
        count_base = select(func.count(ActivityFeedEntry.id)).where(ActivityFeedEntry.organization_id == org_id)

        if event_type:
            base = base.where(ActivityFeedEntry.event_type == event_type)
            count_base = count_base.where(ActivityFeedEntry.event_type == event_type)
        if entity_type:
            base = base.where(ActivityFeedEntry.entity_type == entity_type)
            count_base = count_base.where(ActivityFeedEntry.entity_type == entity_type)
        if user_id is not None:
            base = base.where(ActivityFeedEntry.user_id == user_id)
            count_base = count_base.where(ActivityFeedEntry.user_id == user_id)
        if start_date:
            base = base.where(func.date(ActivityFeedEntry.created_at) >= start_date)
            count_base = count_base.where(func.date(ActivityFeedEntry.created_at) >= start_date)
        if end_date:
            base = base.where(func.date(ActivityFeedEntry.created_at) <= end_date)
            count_base = count_base.where(func.date(ActivityFeedEntry.created_at) <= end_date)

        count_result = await session.execute(count_base)
        total = count_result.scalar() or 0

        result = await session.execute(
            base.options(selectinload(ActivityFeedEntry.user))
            .order_by(ActivityFeedEntry.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        events = list(result.scalars().all())
        return events, total
