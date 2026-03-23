from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.activity_feed import ActivityFeedItem, ActivityFeedListResponse
from app.services.activity_feed_service import ActivityFeedService
from app.services.event_types import EVENT_SEVERITY

router = APIRouter(prefix="/api/activity-feed", tags=["activity-feed"])


@router.get("", response_model=ActivityFeedListResponse)
async def list_activity_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    event_type: str | None = Query(None),
    entity_type: str | None = Query(None),
    user_id: int | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: dict = require_permission("experiments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = current_user["org_id"]
    events, total = await ActivityFeedService.list_events(
        session,
        org_id,
        page,
        page_size,
        event_type=event_type,
        entity_type=entity_type,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
    )
    return ActivityFeedListResponse(
        events=[
            ActivityFeedItem(
                id=e.id,
                user_id=e.user_id,
                user_email=e.user.email if e.user else None,
                event_type=e.event_type,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                summary=e.summary,
                severity=EVENT_SEVERITY.get(e.event_type, "info"),
                metadata_json=e.metadata_json,
                created_at=e.created_at,
            )
            for e in events
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
