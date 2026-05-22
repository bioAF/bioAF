from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.activity_feed import ActivityFeedEntry
from app.models.notification import Notification
from app.services.notification_backfill import backfill_notification_entity_refs


@pytest.mark.asyncio
async def test_backfill_copies_entity_ref_from_activity_feed(session, admin_user):
    org = admin_user.organization_id
    ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    n = Notification(
        organization_id=org,
        user_id=admin_user.id,
        event_type="pipeline.completed",
        title="Pipeline done",
        metadata_json={},
        created_at=ts,
    )
    feed = ActivityFeedEntry(
        organization_id=org,
        user_id=admin_user.id,
        event_type="pipeline.completed",
        entity_type="pipeline_run",
        entity_id=999,
        summary="Pipeline done",
        created_at=ts,
    )
    session.add_all([n, feed])
    await session.commit()

    await backfill_notification_entity_refs(session)
    await session.commit()
    await session.refresh(n)

    assert n.metadata_json.get("entity_type") == "pipeline_run"
    assert n.metadata_json.get("entity_id") == 999


@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_existing_reference(session, admin_user):
    org = admin_user.organization_id
    ts = datetime(2026, 5, 2, 9, 0, 0, tzinfo=timezone.utc)
    n = Notification(
        organization_id=org,
        user_id=admin_user.id,
        event_type="data.uploaded",
        title="Upload",
        metadata_json={"entity_type": "file", "entity_id": 1},
        created_at=ts,
    )
    feed = ActivityFeedEntry(
        organization_id=org,
        user_id=admin_user.id,
        event_type="data.uploaded",
        entity_type="experiment",
        entity_id=42,
        summary="Upload",
        created_at=ts,
    )
    session.add_all([n, feed])
    await session.commit()

    await backfill_notification_entity_refs(session)
    await session.commit()
    await session.refresh(n)

    assert n.metadata_json["entity_type"] == "file"
    assert n.metadata_json["entity_id"] == 1


@pytest.mark.asyncio
async def test_backfill_leaves_unmatched_notifications_untouched(session, admin_user):
    org = admin_user.organization_id
    ts = datetime(2026, 5, 3, 9, 0, 0, tzinfo=timezone.utc)
    n = Notification(
        organization_id=org,
        user_id=admin_user.id,
        event_type="budget.threshold_80",
        title="Budget",
        metadata_json={},
        created_at=ts,
    )
    session.add(n)
    await session.commit()

    await backfill_notification_entity_refs(session)
    await session.commit()
    await session.refresh(n)

    assert "entity_type" not in n.metadata_json
