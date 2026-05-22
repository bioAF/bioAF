import pytest
from httpx import AsyncClient

from app.services.event_bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_subscribe_and_emit():
    """Test basic event bus subscribe/emit."""
    bus = EventBus()
    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("test.event", handler)
    await bus.emit("test.event", {"key": "value"})

    assert len(received) == 1
    assert received[0]["key"] == "value"


@pytest.mark.asyncio
async def test_event_bus_multiple_subscribers():
    """Test multiple subscribers receive the same event."""
    bus = EventBus()
    results = []

    async def handler1(payload):
        results.append("h1")

    async def handler2(payload):
        results.append("h2")

    bus.subscribe("test.multi", handler1)
    bus.subscribe("test.multi", handler2)
    await bus.emit("test.multi", {})

    assert len(results) == 2
    assert "h1" in results
    assert "h2" in results


@pytest.mark.asyncio
async def test_event_bus_failing_subscriber_doesnt_block_others():
    """One failing subscriber should not prevent others from running."""
    bus = EventBus()
    results = []

    async def bad_handler(payload):
        raise ValueError("boom")

    async def good_handler(payload):
        results.append("ok")

    bus.subscribe("test.fail", bad_handler)
    bus.subscribe("test.fail", good_handler)
    await bus.emit("test.fail", {})

    assert results == ["ok"]


@pytest.mark.asyncio
async def test_event_bus_no_subscribers():
    """Emitting to an event with no subscribers should not error."""
    bus = EventBus()
    await bus.emit("test.noone", {"data": 1})


@pytest.mark.asyncio
async def test_list_notifications_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/notifications",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["notifications"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_unread_count_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/notifications/unread-count",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.asyncio
async def test_mark_all_read(client: AsyncClient, admin_token: str, admin_user, session):
    """Create a notification directly, then mark all as read."""
    from app.models.notification import Notification

    n = Notification(
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        event_type="test.event",
        title="Test notification",
        message="Test message",
        severity="info",
    )
    session.add(n)
    await session.flush()
    await session.commit()

    response = await client.post(
        "/api/notifications/mark-all-read",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["marked_read"] >= 1


@pytest.mark.asyncio
async def test_mark_single_read(client: AsyncClient, admin_token: str, admin_user, session):
    from app.models.notification import Notification

    n = Notification(
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        event_type="test.event",
        title="Read me",
        message="Please read",
        severity="info",
    )
    session.add(n)
    await session.flush()
    await session.commit()

    response = await client.patch(
        f"/api/notifications/{n.id}/read",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["read"] is True


@pytest.mark.asyncio
async def test_delete_notification(client: AsyncClient, admin_token: str, admin_user, session):
    from app.models.notification import Notification

    n = Notification(
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        event_type="test.event",
        title="Delete me",
        severity="info",
    )
    session.add(n)
    await session.flush()
    await session.commit()

    response = await client.delete(
        f"/api/notifications/{n.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True


@pytest.mark.asyncio
async def test_notification_not_found(client: AsyncClient, admin_token: str):
    response = await client.patch(
        "/api/notifications/99999/read",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_preferences_crud(client: AsyncClient, admin_token: str):
    # Get (initially empty)
    response = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []

    # Update
    response = await client.put(
        "/api/notifications/preferences",
        json={
            "preferences": [
                {"event_type": "pipeline.completed", "channel": "email", "enabled": True},
                {"event_type": "pipeline.failed", "channel": "slack", "enabled": False},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Get updated
    response = await client.get(
        "/api/notifications/preferences",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_rules_crud_admin(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/notifications/rules",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    response = await client.put(
        "/api/notifications/rules",
        json={
            "rules": [
                {"event_type": "backup.failure", "channel": "email", "role_filter": "admin", "mandatory": True},
            ]
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["mandatory"] is True


@pytest.mark.asyncio
async def test_rules_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/notifications/rules",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_slack_webhook_crud(client: AsyncClient, admin_token: str):
    # Create
    response = await client.post(
        "/api/notifications/slack-webhooks",
        json={
            "name": "Test Webhook",
            "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
            "channel_name": "#bioaf-alerts",
            "event_types": ["pipeline.failed", "backup.failure"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    webhook_id = response.json()["id"]

    # List
    response = await client.get(
        "/api/notifications/slack-webhooks",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Update
    response = await client.put(
        f"/api/notifications/slack-webhooks/{webhook_id}",
        json={"name": "Updated Webhook"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Webhook"

    # Delete
    response = await client.delete(
        f"/api/notifications/slack-webhooks/{webhook_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_test_delivery(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/notifications/test",
        json={"channel": "in_app"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["channel"] == "in_app"


@pytest.mark.asyncio
async def test_in_app_notification_carries_entity_reference(session, admin_user):
    """An in-app notification must carry the associated entity in metadata_json so
    the UI can deep-link to it, even when the event sets no explicit metadata."""
    import app.database as _database
    from sqlalchemy import select

    from app.models.notification import Notification
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    org_id = admin_user.organization_id
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": org_id,
            "target_user_id": admin_user.id,
            "entity_type": "pipeline_run",
            "entity_id": 4242,
            "title": "Pipeline 'scrnaseq' completed",
            "message": "Run 4242 finished successfully",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(Notification.event_type == PIPELINE_COMPLETED)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    n = next(r for r in rows if r.user_id == admin_user.id)
    assert n.metadata_json.get("entity_type") == "pipeline_run"
    assert n.metadata_json.get("entity_id") == 4242


@pytest.mark.asyncio
async def test_in_app_notification_preserves_explicit_metadata(session, admin_user):
    """Enriching with the entity reference must not drop explicit metadata keys."""
    import app.database as _database
    from sqlalchemy import select

    from app.models.notification import Notification
    from app.services.event_types import FILES_CATALOGED
    from app.services.notification_router import NotificationRouter

    org_id = admin_user.organization_id
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": FILES_CATALOGED,
            "org_id": org_id,
            "target_user_id": admin_user.id,
            "entity_type": "ingest_event",
            "entity_id": 7,
            "metadata": {"file_id": 99, "file_type": "fastq"},
            "title": "File cataloged: sample_R1.fastq.gz",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(Notification.event_type == FILES_CATALOGED)
            )
        )
        .scalars()
        .all()
    )
    n = next(r for r in rows if r.user_id == admin_user.id)
    assert n.metadata_json.get("file_id") == 99
    assert n.metadata_json.get("file_type") == "fastq"
    assert n.metadata_json.get("entity_type") == "ingest_event"
    assert n.metadata_json.get("entity_id") == 7


@pytest.mark.asyncio
async def test_filter_notifications(client: AsyncClient, admin_token: str, admin_user, session):
    from app.models.notification import Notification

    for i, sev in enumerate(["info", "warning", "critical"]):
        n = Notification(
            organization_id=admin_user.organization_id,
            user_id=admin_user.id,
            event_type="test.filter",
            title=f"Notification {i}",
            severity=sev,
        )
        session.add(n)
    await session.flush()
    await session.commit()

    # Filter by severity
    response = await client.get(
        "/api/notifications?severity=critical",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    for n in response.json()["notifications"]:
        assert n["severity"] == "critical"
