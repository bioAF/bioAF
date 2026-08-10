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
        (await session.execute(select(Notification).where(Notification.event_type == PIPELINE_COMPLETED)))
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    n = next(r for r in rows if r.user_id == admin_user.id)
    assert n.metadata_json.get("entity_type") == "pipeline_run"
    assert n.metadata_json.get("entity_id") == 4242


@pytest.mark.asyncio
async def test_notification_response_serializes_entity_reference():
    """The API schema must expose metadata_json (with the entity reference) so the
    frontend can build the deep link."""
    from types import SimpleNamespace
    from datetime import datetime, timezone

    from app.schemas.notification import NotificationResponse

    row = SimpleNamespace(
        id=1,
        event_type="pipeline.completed",
        title="Pipeline 'scrnaseq' completed",
        message="Run 555 finished",
        severity="info",
        read=False,
        read_at=None,
        metadata_json={"entity_type": "pipeline_run", "entity_id": 555},
        created_at=datetime.now(timezone.utc),
    )
    resp = NotificationResponse.model_validate(row)
    assert resp.metadata_json == {"entity_type": "pipeline_run", "entity_id": 555}


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
        (await session.execute(select(Notification).where(Notification.event_type == FILES_CATALOGED))).scalars().all()
    )
    n = next(r for r in rows if r.user_id == admin_user.id)
    assert n.metadata_json.get("file_id") == 99
    assert n.metadata_json.get("file_type") == "fastq"
    assert n.metadata_json.get("entity_type") == "ingest_event"
    assert n.metadata_json.get("entity_id") == 7


@pytest.mark.asyncio
async def test_in_app_notification_suppressed_when_preference_disabled(session, admin_user):
    """Turning OFF the in-app toggle for an event must actually stop the in-app notification."""
    import app.database as _database
    from sqlalchemy import select

    from app.models.notification import Notification, NotificationPreference
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    session.add(
        NotificationPreference(user_id=admin_user.id, event_type=PIPELINE_COMPLETED, channel="in_app", enabled=False)
    )
    await session.commit()

    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline completed",
            "message": "done",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.event_type == PIPELINE_COMPLETED, Notification.user_id == admin_user.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # the disabled in-app preference is honored


def _email_spy(monkeypatch):
    from app.services.notification_channels.email_adapter import EmailChannel

    calls: list[str] = []

    async def _deliver(to, title, message, severity):
        calls.append(to)
        return True

    monkeypatch.setattr(EmailChannel, "deliver", _deliver)
    return calls


@pytest.mark.asyncio
async def test_email_opt_in_delivers_without_an_org_rule(session, admin_user, monkeypatch):
    """An explicit email opt-in delivers via the configured SMTP with NO org NotificationRule
    required - the old rule gate was why email never sent at all."""
    import app.database as _database
    from app.models.notification import NotificationPreference
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    session.add(
        NotificationPreference(user_id=admin_user.id, event_type=PIPELINE_COMPLETED, channel="email", enabled=True)
    )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline completed",
            "message": "done",
        }
    )
    assert admin_user.email in calls


@pytest.mark.asyncio
async def test_email_not_sent_without_an_explicit_opt_in(session, admin_user, monkeypatch):
    """Email is OPT-IN: no preference row means no email.

    Regression. Making email "preference-driven, default on" silently opted every user into email
    for every one of the ~40 event types (and `_resolve_recipients` fans out to all org admins when
    no rule exists), so users got mail they never asked for. In-app is the default channel; email
    is only sent where the user turned it on.
    """
    import app.database as _database
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline completed",
            "message": "done",
        }
    )
    assert admin_user.email not in calls


@pytest.mark.asyncio
async def test_review_reminder_in_app_on_email_off(session, admin_user, monkeypatch):
    """The reported configuration end to end: in-app ON, email OFF for review reminders.

    Expect the in-app notification to be created and NO email. Pins the two channels to their own
    preference rows so one can never be served by the other's setting.
    """
    import app.database as _database
    from sqlalchemy import select
    from app.models.notification import Notification, NotificationPreference
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_router import NotificationRouter

    session.add(
        NotificationPreference(
            user_id=admin_user.id,
            event_type=PIPELINE_RUN_REVIEW_REMINDER,
            channel="in_app",
            enabled=True,
        )
    )
    session.add(
        NotificationPreference(
            user_id=admin_user.id,
            event_type=PIPELINE_RUN_REVIEW_REMINDER,
            channel="email",
            enabled=False,
        )
    )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.event_type == PIPELINE_RUN_REVIEW_REMINDER,
                    Notification.user_id == admin_user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # in-app delivered
    assert admin_user.email not in calls  # and NOT diverted to email


@pytest.mark.asyncio
async def test_email_not_sent_when_preference_disabled(session, admin_user, monkeypatch):
    import app.database as _database
    from app.models.notification import NotificationPreference
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    session.add(
        NotificationPreference(user_id=admin_user.id, event_type=PIPELINE_COMPLETED, channel="email", enabled=False)
    )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline completed",
            "message": "done",
        }
    )
    assert admin_user.email not in calls  # disabled email preference is honored


@pytest.mark.asyncio
async def test_every_selected_channel_delivers(session, admin_user, monkeypatch):
    """Rule 4: a combination of enabled channels delivers to ALL of them, not just one."""
    import app.database as _database
    from sqlalchemy import select
    from app.models.notification import Notification, NotificationPreference
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_router import NotificationRouter

    for channel in ("in_app", "email"):
        session.add(
            NotificationPreference(
                user_id=admin_user.id,
                event_type=PIPELINE_RUN_REVIEW_REMINDER,
                channel=channel,
                enabled=True,
            )
        )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.event_type == PIPELINE_RUN_REVIEW_REMINDER,
                    Notification.user_id == admin_user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert admin_user.email in calls


@pytest.mark.asyncio
async def test_disabled_on_every_channel_delivers_nothing(session, admin_user, monkeypatch):
    """Rules 1+3: turning a notification off must silence it, NOT re-route it to another avenue.

    This is the shape of the reported bug: in-app was off, so the notification "went to email
    instead". Nothing may fall back to another channel when a channel is disabled.
    """
    import app.database as _database
    from sqlalchemy import select
    from app.models.notification import Notification, NotificationPreference
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_router import NotificationRouter

    for channel in ("in_app", "email", "slack"):
        session.add(
            NotificationPreference(
                user_id=admin_user.id,
                event_type=PIPELINE_RUN_REVIEW_REMINDER,
                channel=channel,
                enabled=False,
            )
        )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    rows = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.event_type == PIPELINE_RUN_REVIEW_REMINDER,
                    Notification.user_id == admin_user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
    assert calls == []


@pytest.mark.asyncio
async def test_disabling_in_app_does_not_also_disable_slack(session, admin_user, monkeypatch):
    """Rules 1+4: the channels are independent. Turning OFF in-app must not silence Slack too.

    Regression: Slack delivery via OAuth channel mappings was gated on `first_notification_id is not
    None`, which only exists to anchor the delivery log. Once InAppChannel started returning None for
    a suppressed in-app preference, a user turning in-app off silently killed the org's Slack posts.
    """
    import app.database as _database
    from app.models.notification import (
        NotificationPreference,
        SlackChannelMapping,
        SlackInstallation,
    )
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_channels.slack_adapter import SlackChannel
    from app.services.notification_router import NotificationRouter

    session.add(
        SlackInstallation(
            organization_id=admin_user.organization_id,
            team_id="T1",
            team_name="Lab",
            bot_token="xoxb-test",
            bot_user_id="U1",
            installed_by=admin_user.id,
            enabled=True,
        )
    )
    session.add(
        SlackChannelMapping(
            organization_id=admin_user.organization_id,
            channel_id="C1",
            channel_name="lab-alerts",
            event_types_json=[],
            enabled=True,
        )
    )
    session.add(
        NotificationPreference(
            user_id=admin_user.id,
            event_type=PIPELINE_RUN_REVIEW_REMINDER,
            channel="in_app",
            enabled=False,
        )
    )
    await session.commit()

    posted: list[str] = []

    async def _deliver(bot_token, channel_id, title, message, severity):
        posted.append(channel_id)
        return True, None

    monkeypatch.setattr(SlackChannel, "deliver", _deliver)

    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    assert posted == ["C1"]  # Slack still posts; in-app being off is not Slack's business


@pytest.mark.asyncio
async def test_slack_channel_posts_are_org_level_not_per_user(session, admin_user, monkeypatch):
    """A Slack post goes to a shared org channel, so it is NOT gated by one user's preference.

    Routing is per channel: Settings -> Slack picks the event types for each channel. The per-user
    slack preference is consulted only for org NotificationRules. The profile page keeps its Slack
    column (admin-only, since Slack routing is an admin concern) and the preferences are stored;
    this pins that an OAuth channel mapping still posts regardless, so nobody expects one user's
    toggle to silence a shared channel.
    """
    import app.database as _database
    from app.models.notification import (
        NotificationPreference,
        SlackChannelMapping,
        SlackInstallation,
    )
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_channels.slack_adapter import SlackChannel
    from app.services.notification_router import NotificationRouter

    session.add(
        SlackInstallation(
            organization_id=admin_user.organization_id,
            team_id="T1",
            team_name="Lab",
            bot_token="xoxb-test",
            bot_user_id="U1",
            installed_by=admin_user.id,
            enabled=True,
        )
    )
    session.add(
        SlackChannelMapping(
            organization_id=admin_user.organization_id,
            channel_id="C1",
            channel_name="lab-alerts",
            event_types_json=[],
            enabled=True,
        )
    )
    session.add(
        NotificationPreference(
            user_id=admin_user.id,
            event_type=PIPELINE_RUN_REVIEW_REMINDER,
            channel="slack",
            enabled=False,
        )
    )
    await session.commit()

    posted: list[str] = []

    async def _deliver(bot_token, channel_id, title, message, severity):
        posted.append(channel_id)
        return True, None

    monkeypatch.setattr(SlackChannel, "deliver", _deliver)

    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    assert posted == ["C1"]


@pytest.mark.asyncio
async def test_each_recipient_gets_their_own_email(session, admin_user, monkeypatch):
    """Rule 5: one message per recipient. Never several users on one To, which would let a
    reply-all storm start (and leaks the recipient list)."""
    import app.database as _database
    from app.models.notification import NotificationPreference
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_router import NotificationRouter

    role_map = admin_user._test_role_map  # type: ignore[attr-defined]
    second = User(
        email="second-admin@test.com",
        password_hash=AuthService.hash_password("testpassword123"),
        role_id=role_map["admin"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(second)
    await session.flush()

    for user_id in (admin_user.id, second.id):
        session.add(
            NotificationPreference(
                user_id=user_id,
                event_type=PIPELINE_RUN_REVIEW_REMINDER,
                channel="email",
                enabled=True,
            )
        )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_RUN_REVIEW_REMINDER,
            "org_id": admin_user.organization_id,
            "title": "Pipeline run awaiting review (72h)",
            "message": "not reviewed",
        }
    )

    assert sorted(calls) == sorted([admin_user.email, second.email])
    for to in calls:
        assert "," not in to  # each send addresses exactly one mailbox


def test_every_deliverable_event_type_has_a_preference_toggle():
    """Rule 1 needs a switch to exist: a notification a user cannot turn off is not a preference.

    The profile page listed 35 of the 60 event types users can actually receive. The other 25 - the
    whole literature feature, sequencing batches, work nodes, auto-run, and the SDR/glossary
    notifications raised outside the event bus - had no toggle at all, so they could not be turned
    off by anyone. Keeps the page and the emitters from drifting apart again.
    """
    import pathlib
    import re

    from app.services.event_types import USER_CONFIGURABLE_EVENT_TYPES

    ui = pathlib.Path(__file__).parents[2] / "frontend/src/app/(app)/profile/components/NotificationsTab.tsx"
    listed = set(re.findall(r'\{ type: "([^"]+)"', ui.read_text()))

    missing = sorted(set(USER_CONFIGURABLE_EVENT_TYPES) - listed)
    assert missing == [], f"event types with no preference toggle: {missing}"

    unknown = sorted(listed - set(USER_CONFIGURABLE_EVENT_TYPES))
    assert unknown == [], f"toggles for event types nothing emits: {unknown}"


def test_email_message_addresses_a_single_recipient():
    """Rule 5, at the wire: a one-recipient send puts that address in To."""
    from app.services.notification_channels.email_adapter import build_message

    msg = build_message("one@example.com", "Title", "Body", "info")
    assert msg["To"] == "one@example.com"
    assert not msg["Bcc"]


def test_email_message_with_several_recipients_uses_bcc():
    """Rule 5: if a caller ever hands several recipients to one message, they go to Bcc so nobody
    can reply-all and no recipient sees the others."""
    from app.services.notification_channels.email_adapter import build_message

    msg = build_message(["a@example.com", "b@example.com"], "Title", "Body", "info")
    assert msg["Bcc"] == "a@example.com, b@example.com"
    assert "a@example.com" not in (msg["To"] or "")
    assert "b@example.com" not in (msg["To"] or "")


@pytest.mark.asyncio
async def test_saving_some_preferences_does_not_wipe_the_rest(session, admin_user):
    """A save that carries only part of the user's preferences must not delete the others.

    update_preferences used to DELETE every row for the user and re-insert the payload, so a page
    that saved from a partial view (for example after its load failed and left the form at defaults)
    silently wiped every stored preference the user had.
    """
    from sqlalchemy import select
    from app.models.notification import NotificationPreference
    from app.services.event_types import PIPELINE_COMPLETED, PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_service import NotificationService

    session.add(
        NotificationPreference(user_id=admin_user.id, event_type=PIPELINE_COMPLETED, channel="email", enabled=False)
    )
    await session.commit()

    await NotificationService.update_preferences(
        session,
        admin_user.id,
        [{"event_type": PIPELINE_RUN_REVIEW_REMINDER, "channel": "in_app", "enabled": True}],
    )
    await session.commit()

    rows = (
        (await session.execute(select(NotificationPreference).where(NotificationPreference.user_id == admin_user.id)))
        .scalars()
        .all()
    )
    stored = {(r.event_type, r.channel): r.enabled for r in rows}
    assert stored[(PIPELINE_COMPLETED, "email")] is False  # untouched row survived
    assert stored[(PIPELINE_RUN_REVIEW_REMINDER, "in_app")] is True


@pytest.mark.asyncio
async def test_resaving_a_preference_updates_it_in_place(session, admin_user):
    """Re-enabling a channel the user had turned off must actually flip the stored row."""
    from sqlalchemy import select
    from app.models.notification import NotificationPreference
    from app.services.event_types import PIPELINE_RUN_REVIEW_REMINDER
    from app.services.notification_service import NotificationService

    session.add(
        NotificationPreference(
            user_id=admin_user.id,
            event_type=PIPELINE_RUN_REVIEW_REMINDER,
            channel="in_app",
            enabled=False,
        )
    )
    await session.commit()

    await NotificationService.update_preferences(
        session,
        admin_user.id,
        [{"event_type": PIPELINE_RUN_REVIEW_REMINDER, "channel": "in_app", "enabled": True}],
    )
    await session.commit()

    rows = (
        (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == admin_user.id,
                    NotificationPreference.event_type == PIPELINE_RUN_REVIEW_REMINDER,
                    NotificationPreference.channel == "in_app",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0].enabled is True


@pytest.mark.asyncio
async def test_mandatory_email_rule_overrides_disabled_preference(session, admin_user, monkeypatch):
    """A mandatory org email rule still forces delivery even when the user disabled the preference."""
    import app.database as _database
    from app.models.notification import NotificationPreference, NotificationRule
    from app.services.event_types import PIPELINE_COMPLETED
    from app.services.notification_router import NotificationRouter

    session.add(
        NotificationRule(
            organization_id=admin_user.organization_id,
            event_type=PIPELINE_COMPLETED,
            channel="email",
            mandatory=True,
            enabled=True,
        )
    )
    session.add(
        NotificationPreference(user_id=admin_user.id, event_type=PIPELINE_COMPLETED, channel="email", enabled=False)
    )
    await session.commit()

    calls = _email_spy(monkeypatch)
    router = NotificationRouter(_database.async_session_factory)
    await router._handle_event(
        {
            "event_type": PIPELINE_COMPLETED,
            "org_id": admin_user.organization_id,
            "target_user_id": admin_user.id,
            "title": "Pipeline completed",
            "message": "done",
        }
    )
    assert admin_user.email in calls


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
