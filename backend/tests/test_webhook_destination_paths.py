"""Every webhook path applies the destination policy (01A).

Subscription create/update/reactivation, test and replay refuse an unauthorized destination before
anything is queued; the worker re-authorizes on every attempt, including subscriptions stored
before validation existed, and records only safe codes.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import webhook_dispatcher, webhook_service, webhook_worker
from app.services.event_types import INTEGRATION_EXPERIMENT_CREATED, WEBHOOK_EXPERIMENT_CREATED
from app.services.webhook_transport import DestinationRefused

pytestmark = pytest.mark.asyncio


async def _create(session, user, url="https://hooks.example/h"):
    sub, secret = await webhook_service.create_subscription(
        session,
        org_id=user.organization_id,
        name="LIMS",
        url=url,
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=user.id,
    )
    await session.commit()
    return sub, secret


async def _legacy(session, user, url: str, *, active=True) -> WebhookSubscription:
    """A subscription stored before destination validation existed."""
    row = WebhookSubscription(
        organization_id=user.organization_id,
        name="legacy",
        url=url,
        secret="whsec_legacy",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=user.id,
        is_active=active,
    )
    session.add(row)
    await session.commit()
    return row


async def _dispatch(user):
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": user.organization_id, "data": {"experiment_id": 1}}
    )


async def _delivery(session, sub_id) -> WebhookDelivery:
    row = (await session.execute(select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub_id))).scalar_one()
    await session.refresh(row)
    return row


# --- subscription changes ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://10.0.0.5/h", "destination_denied"),
        ("https://169.254.169.254/latest/meta-data", "destination_denied"),
        ("http://hooks.example/h", "invalid_destination"),
        ("https://u:p@hooks.example/h", "invalid_destination"),
        ("https://unresolvable.test/h", "dns_failed"),
    ],
)
async def test_create_refuses_unauthorized_destinations(session, admin_user, url, code):
    with pytest.raises(DestinationRefused) as err:
        await webhook_service.create_subscription(
            session,
            org_id=admin_user.organization_id,
            name="bad",
            url=url,
            events=[WEBHOOK_EXPERIMENT_CREATED],
            created_by_user_id=admin_user.id,
        )
    assert err.value.code == code
    await session.rollback()
    assert (await session.execute(select(WebhookSubscription))).scalars().all() == []


async def test_create_resolving_to_private_is_refused(session, admin_user, webhook_network):
    webhook_network.answers["inside.example"] = ["10.1.2.3"]
    with pytest.raises(DestinationRefused):
        await _create(session, admin_user, "https://inside.example/h")


async def test_audit_records_no_url(session, admin_user):
    sub, _ = await _create(session, admin_user, "https://hooks.example/secret-path?token=abc")
    await webhook_service.update_subscription(
        session, sub.id, admin_user.organization_id, admin_user.id, url="https://hooks.example/other?token=xyz"
    )
    await session.commit()
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.entity_type == "webhook_subscription"))).scalars().all()
    )
    text = json.dumps([r.details_json for r in rows])
    assert "secret-path" not in text and "token" not in text and "hooks.example" not in text
    assert [r.details_json.get("url_changed") for r in rows] == [True, True]


async def test_changing_to_a_denied_url_keeps_the_previous_configuration(session, admin_user):
    sub, _ = await _create(session, admin_user)
    sub_id, org_id, user_id = sub.id, admin_user.organization_id, admin_user.id
    with pytest.raises(DestinationRefused):
        await webhook_service.update_subscription(
            session, sub_id, org_id, user_id, url="https://127.0.0.1/h", name="renamed"
        )
    await session.rollback()
    fresh = (await session.execute(select(WebhookSubscription).where(WebhookSubscription.id == sub_id))).scalar_one()
    assert fresh.url == "https://hooks.example/h" and fresh.name == "LIMS"


async def test_transient_resolution_failure_rejects_the_change_safely(session, admin_user, webhook_network):
    sub, _ = await _create(session, admin_user)
    with pytest.raises(DestinationRefused) as err:
        await webhook_service.update_subscription(
            session, sub.id, admin_user.organization_id, admin_user.id, url="https://flaky.test/h"
        )
    assert err.value.code == "dns_failed"


async def test_reactivating_an_unsafe_subscription_is_refused_but_disabling_is_allowed(session, admin_user):
    legacy = await _legacy(session, admin_user, "https://127.0.0.1/h", active=True)
    disabled = await webhook_service.disable_subscription(session, legacy.id, admin_user.organization_id, admin_user.id)
    await session.commit()
    assert disabled.is_active is False

    with pytest.raises(DestinationRefused):
        await webhook_service.update_subscription(
            session, legacy.id, admin_user.organization_id, admin_user.id, is_active=True
        )


async def test_test_event_and_replay_refuse_an_unsafe_subscription(session, admin_user):
    legacy = await _legacy(session, admin_user, "https://10.0.0.9/h")
    legacy_id, org_id, user_id = legacy.id, admin_user.organization_id, admin_user.id
    with pytest.raises(DestinationRefused):
        await webhook_service.fire_test_event(session, legacy_id, org_id, user_id)
    await session.rollback()

    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": org_id, "data": {"experiment_id": 1}}
    )
    original = await _delivery(session, legacy_id)
    with pytest.raises(DestinationRefused):
        await webhook_service.replay_delivery(session, original.id, user_id)
    await session.rollback()
    count = len((await session.execute(select(WebhookDelivery))).scalars().all())
    assert count == 1


# --- worker -----------------------------------------------------------------------------------


async def test_a_legacy_unsafe_subscription_fails_terminally_without_a_dial(session, admin_user, webhook_network):
    legacy = await _legacy(session, admin_user, "https://169.254.169.254/latest")
    await _dispatch(admin_user)

    await webhook_worker._drain_once()

    d = await _delivery(session, legacy.id)
    assert d.status == "failed"
    assert d.attempt_count == 1
    assert d.next_attempt_at is None
    assert d.last_response_status is None
    assert d.last_response_body == "destination_denied"
    assert webhook_network.dials == []
    # Still visible for an administrator to correct and replay.
    assert (await session.get(WebhookSubscription, legacy.id)) is not None


async def test_a_dns_failure_follows_the_retry_schedule(session, admin_user, webhook_network):
    sub, _ = await _create(session, admin_user)
    webhook_network.answers["hooks.example"] = OSError("resolver down")
    await _dispatch(admin_user)

    await webhook_worker._drain_once()

    d = await _delivery(session, sub.id)
    assert d.status == "pending" and d.attempt_count == 1 and d.next_attempt_at is not None
    assert d.last_response_body == "dns_failed"


async def test_a_subscription_that_now_resolves_privately_is_refused_at_send_time(session, admin_user, webhook_network):
    sub, _ = await _create(session, admin_user)
    webhook_network.answers["hooks.example"] = ["10.0.0.5"]
    await _dispatch(admin_user)

    await webhook_worker._drain_once()

    d = await _delivery(session, sub.id)
    assert d.status == "failed" and d.last_response_body == "destination_denied"
    assert webhook_network.dials == []


async def test_non_2xx_and_transport_errors_store_safe_codes_only(session, admin_user):
    sub, _ = await _create(session, admin_user)
    await _dispatch(admin_user)
    with respx.mock:
        respx.post("https://hooks.example/h").mock(return_value=httpx.Response(500, text="secret receiver detail"))
        await webhook_worker._drain_once()
    d = await _delivery(session, sub.id)
    assert d.last_response_status == 500 and d.last_response_body == "http_non_2xx"

    d.next_attempt_at = d.created_at
    await session.commit()
    with respx.mock:
        respx.post("https://hooks.example/h").mock(side_effect=httpx.ConnectError("10.0.0.1 refused secret"))
        await webhook_worker._drain_once()
    d = await _delivery(session, sub.id)
    assert d.last_response_status is None and d.last_response_body == "transport_failed"
    assert d.status == "pending" and d.attempt_count == 2


async def test_signed_bytes_are_unchanged(session, admin_user):
    sub, secret = await _create(session, admin_user)
    await _dispatch(admin_user)
    with respx.mock:
        route = respx.post("https://hooks.example/h").mock(return_value=httpx.Response(204))
        await webhook_worker._drain_once()
    request = route.calls.last.request
    d = await _delivery(session, sub.id)
    body = json.dumps(d.payload_json, sort_keys=True).encode()
    assert request.content == body
    t, v1 = request.headers["X-bioAF-Signature"].split(",")
    expected = hmac.new(secret.encode(), f"{t[2:]}.".encode() + body, hashlib.sha256).hexdigest()
    assert v1 == f"v1={expected}"
    assert d.status == "delivered"


# --- API --------------------------------------------------------------------------------------


async def test_api_refuses_denied_destinations_and_offers_no_override(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/admin/webhooks",
        headers=headers,
        json={
            "name": "x",
            "url": "https://10.24.8.19/h",
            "events": [WEBHOOK_EXPERIMENT_CREATED],
            "internal_destinations": [{"origin": "https://10.24.8.19", "cidrs": ["10.24.8.19/32"]}],
            "cidrs": ["10.24.8.19/32"],
        },
    )
    assert r.status_code == 422
    assert "destination_denied" in r.text


async def test_api_replay_and_test_refuse_unsafe_subscriptions(client, admin_token, admin_user, session):
    headers = {"Authorization": f"Bearer {admin_token}"}
    legacy = await _legacy(session, admin_user, "https://127.0.0.1/h")
    await _dispatch(admin_user)
    original = await _delivery(session, legacy.id)

    r = await client.post(f"/api/admin/webhooks/{legacy.id}/test", headers=headers)
    assert r.status_code == 422
    r = await client.post(f"/api/admin/webhooks/deliveries/{original.id}/replay", headers=headers)
    assert r.status_code == 422
