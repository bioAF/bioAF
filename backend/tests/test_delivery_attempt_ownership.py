"""Characterization: who owns delivery retries for outbound notifications.

Persisted integration webhooks own their retry schedule in the database: each
drain sends exactly one HTTP request per recorded attempt, whatever the
failure, so no transport-level retry multiplies delivery counts. Slack's
in-process loops are pinned here as they behave today, before any change to
their policy.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.webhook import WebhookDelivery
from app.services import webhook_dispatcher, webhook_service, webhook_worker
from app.services.event_types import INTEGRATION_EXPERIMENT_CREATED, WEBHOOK_EXPERIMENT_CREATED
from app.services.notification_channels import slack_adapter
from app.services.notification_channels.slack_adapter import SlackChannel

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.ConnectError("x"),
        httpx.ReadTimeout("x"),
    ],
    ids=["503", "429", "connect", "read_timeout"],
)
async def test_a_webhook_drain_sends_one_request_per_recorded_attempt(session, admin_user, failure):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://hooks.example/one",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": admin_user.organization_id, "data": {"experiment_id": 1}}
    )
    with respx.mock:
        route = respx.post("https://hooks.example/one")
        if isinstance(failure, Exception):
            route.mock(side_effect=failure)
        else:
            route.mock(return_value=failure)
        await webhook_worker._drain_once()
    delivery = (
        await session.execute(select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id))
    ).scalar_one()
    await session.refresh(delivery)
    assert route.call_count == 1
    assert delivery.attempt_count == 1
    assert delivery.status == "pending" and delivery.next_attempt_at is not None


# --- Slack, as it behaves before any policy change ----------------------------------------


@pytest.fixture
def slack_sleeps(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(slack_adapter.asyncio, "sleep", fake_sleep)
    return delays


async def _deliver():
    return await SlackChannel.deliver("xoxb-SYNTH", "C1", "Title", "Body", "info")


async def test_slack_ok_false_transient_errors_are_retried_without_delay(slack_sleeps):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "ratelimited"})
        )
        assert await _deliver() == (False, "ratelimited")
    assert route.call_count == 3
    assert slack_sleeps == []


async def test_slack_http_429_with_json_body_ignores_retry_after(slack_sleeps):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={"ok": False, "error": "ratelimited"})
        )
        assert await _deliver() == (False, "ratelimited")
    assert route.call_count == 3
    assert slack_sleeps == []


@pytest.mark.parametrize("error", ["channel_not_found", "not_in_channel", "invalid_auth", "token_revoked"])
async def test_slack_known_permanent_errors_stop_at_once(slack_sleeps, error):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": error})
        )
        assert await _deliver() == (False, error)
    assert route.call_count == 1


async def test_other_slack_permanent_errors_are_retried_today(slack_sleeps):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "is_archived"})
        )
        assert await _deliver() == (False, "is_archived")
    assert route.call_count == 3


async def test_slack_transport_failures_retry_with_two_and_eight_second_waits(slack_sleeps):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(side_effect=httpx.ReadTimeout("x"))
        ok, _ = await _deliver()
    assert ok is False
    assert route.call_count == 3
    assert slack_sleeps == [2, 8]


async def test_slack_webhook_client_errors_are_retried_today(slack_sleeps):
    url = "https://hooks.slack.com/services/T/B/X"
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(404, text="no_service"))
        assert await SlackChannel.deliver_webhook(url, "t", "m", "info") is False
    assert route.call_count == 3
    assert slack_sleeps == [2, 8]
