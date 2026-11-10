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


# --- Slack delivery policy (owner decision 2026-11-09) ------------------------------------
#
# Retry only transient failures: HTTP 429 (honoring Retry-After within the budget), 5xx, and
# Slack's transient ok:false codes. Every other Slack error and every webhook 4xx fails at once.
# A send that may already have reached Slack (read timeout, dropped connection) is retried:
# delivery is at-least-once, so a channel can occasionally see a duplicate.


async def _deliver():
    return await SlackChannel.deliver("xoxb-SYNTH", "C1", "Title", "Body", "info")


@pytest.mark.parametrize(
    "error", ["ratelimited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"]
)
async def test_slack_transient_ok_false_errors_retry_with_backoff(request_clock, error):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": error})
        )
        assert await _deliver() == (False, error)
    assert route.call_count == 3
    assert len(request_clock.slept) == 2 and all(d > 0 for d in request_clock.slept)


async def test_slack_transient_error_then_success_delivers_once(request_clock):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(200, json={"ok": False, "error": "internal_error"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        assert await _deliver() == (True, "")
    assert route.call_count == 2


async def test_slack_http_429_honors_retry_after(request_clock):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "7"}, json={"ok": False, "error": "ratelimited"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        assert await _deliver() == (True, "")
    assert route.call_count == 2
    assert request_clock.slept == [7.0]


async def test_a_retry_after_beyond_the_budget_ends_delivery_without_another_send(request_clock):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "3600"}, json={"ok": False, "error": "ratelimited"}
            )
        )
        assert await _deliver() == (False, "ratelimited")
    assert route.call_count == 1


@pytest.mark.parametrize(
    "error", ["channel_not_found", "not_in_channel", "invalid_auth", "token_revoked", "is_archived", "msg_too_long"]
)
async def test_slack_permanent_errors_stop_at_once(request_clock, error):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": error})
        )
        assert await _deliver() == (False, error)
    assert route.call_count == 1
    assert request_clock.slept == []


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("x"), httpx.ConnectError("x"), httpx.Response(503)])
async def test_slack_transport_and_server_failures_retry_at_least_once_semantics(request_clock, failure):
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL)
        if isinstance(failure, Exception):
            route.mock(side_effect=failure)
        else:
            route.mock(return_value=failure)
        ok, error = await _deliver()
    assert ok is False and error
    assert route.call_count == 3


async def test_a_cancelled_slack_delivery_sends_nothing_more():
    import asyncio

    from app.services import request_pacing
    from tests.support.request_clock import ManualClock

    clock = ManualClock()
    request_pacing.reset(clock)
    with respx.mock:
        route = respx.post(slack_adapter.SLACK_POST_MESSAGE_URL).mock(return_value=httpx.Response(503))
        task = asyncio.ensure_future(_deliver())
        await clock.settle()
        assert route.call_count == 1 and clock.sleeping == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await clock.advance(60)
    assert route.call_count == 1


WEBHOOK = "https://hooks.slack.com/services/T/B/X"


@pytest.mark.parametrize("status", [400, 403, 404, 410])
async def test_slack_webhook_client_errors_fail_at_once(request_clock, status):
    with respx.mock:
        route = respx.post(WEBHOOK).mock(return_value=httpx.Response(status, text="no_service"))
        assert await SlackChannel.deliver_webhook(WEBHOOK, "t", "m", "info") is False
    assert route.call_count == 1


@pytest.mark.parametrize("failure", [httpx.Response(500), httpx.Response(429), httpx.ReadTimeout("x")])
async def test_slack_webhook_transient_failures_retry_then_recover(request_clock, failure):
    with respx.mock:
        route = respx.post(WEBHOOK)
        route.mock(side_effect=[failure if isinstance(failure, Exception) else failure, httpx.Response(200, text="ok")])
        assert await SlackChannel.deliver_webhook(WEBHOOK, "t", "m", "info") is True
    assert route.call_count == 2
