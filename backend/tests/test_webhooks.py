"""ADR-051: webhook subscriptions, dispatcher, and worker."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import (
    webhook_dispatcher,
    webhook_service,
    webhook_worker,
)
from app.services.event_bus import event_bus
from app.services.event_types import (
    INTEGRATION_EXPERIMENT_CREATED,
    WEBHOOK_EXPERIMENT_CREATED,
)


@pytest.mark.asyncio
async def test_create_subscription_returns_secret_once(session, admin_user):
    row, secret = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="LIMS pipe",
        url="https://lims.example/hooks",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    assert secret.startswith("whsec_")
    # The encrypted column round-trips back to the same plaintext.
    fresh = (
        await session.execute(
            select(WebhookSubscription).where(WebhookSubscription.id == row.id)
        )
    ).scalar_one()
    assert fresh.secret == secret


@pytest.mark.asyncio
async def test_create_subscription_rejects_unknown_event_type(session, admin_user):
    with pytest.raises(ValueError):
        await webhook_service.create_subscription(
            session,
            org_id=admin_user.organization_id,
            name="bad",
            url="https://x.example",
            events=["pipeline.dancing"],
            created_by_user_id=admin_user.id,
        )


@pytest.mark.asyncio
async def test_dispatcher_creates_one_delivery_per_active_subscription(
    session, admin_user
):
    await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://a.example",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    inactive, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="B",
        url="https://b.example",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    inactive.is_active = False
    await session.commit()

    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {
            "organization_id": admin_user.organization_id,
            "data": {"experiment_id": 42, "external_id": "EXP-1"},
        },
    )

    deliveries = (
        await session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.event_type == WEBHOOK_EXPERIMENT_CREATED
            )
        )
    ).scalars().all()
    assert len(deliveries) == 1
    payload = deliveries[0].payload_json
    assert payload["event"] == WEBHOOK_EXPERIMENT_CREATED
    assert payload["organization_id"] == admin_user.organization_id
    assert payload["data"]["experiment_id"] == 42


@pytest.mark.asyncio
async def test_dispatcher_isolates_orgs(session, admin_user):
    """Org A's events do not reach Org B's subscriptions."""
    from app.models.organization import Organization

    org_b = Organization(name="Other", setup_complete=True)
    session.add(org_b)
    await session.flush()
    await webhook_service.create_subscription(
        session,
        org_id=org_b.id,
        name="B",
        url="https://b.example",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()

    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {
            "organization_id": admin_user.organization_id,
            "data": {"experiment_id": 1},
        },
    )

    deliveries = (await session.execute(select(WebhookDelivery))).scalars().all()
    assert deliveries == []


@pytest.mark.asyncio
async def test_signature_validates(session, admin_user):
    body = b'{"id":"evt_x","event":"experiment.created"}'
    sig = webhook_service.sign_payload("whsec_test", body, timestamp=1717000000)
    assert sig.startswith("t=1717000000,v1=")
    expected = hmac.new(
        b"whsec_test", b"1717000000." + body, hashlib.sha256
    ).hexdigest()
    assert sig.endswith(expected)


@pytest.mark.asyncio
async def test_worker_marks_2xx_as_delivered(session, admin_user, respx_mock):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://hooks.example/path",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {"organization_id": admin_user.organization_id, "data": {"experiment_id": 1}},
    )

    respx_mock.post("https://hooks.example/path").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    await webhook_worker._drain_once()

    delivery = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id)
        )
    ).scalar_one()
    await session.refresh(delivery)
    assert delivery.status == "delivered"
    assert delivery.attempt_count == 1


@pytest.mark.asyncio
async def test_worker_retries_on_5xx(session, admin_user, respx_mock):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://hooks.example/fail",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {"organization_id": admin_user.organization_id, "data": {"experiment_id": 1}},
    )
    respx_mock.post("https://hooks.example/fail").mock(
        return_value=httpx.Response(500, text="bad")
    )
    await webhook_worker._drain_once()

    delivery = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id)
        )
    ).scalar_one()
    await session.refresh(delivery)
    assert delivery.status == "pending"
    assert delivery.attempt_count == 1
    assert delivery.next_attempt_at is not None


@pytest.mark.asyncio
async def test_worker_dead_letters_after_max_attempts(session, admin_user, respx_mock):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://hooks.example/dead",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {"organization_id": admin_user.organization_id, "data": {"experiment_id": 1}},
    )
    respx_mock.post("https://hooks.example/dead").mock(
        return_value=httpx.Response(500, text="bad")
    )

    # Run the worker enough times to exceed MAX_ATTEMPTS. Reset next_attempt_at
    # between iterations so the worker actually picks the row up.
    for _ in range(webhook_service.MAX_ATTEMPTS + 1):
        d = (
            await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id)
            )
        ).scalar_one()
        if d.status != "pending":
            break
        d.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        await webhook_worker._drain_once()

    delivery = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id)
        )
    ).scalar_one()
    await session.refresh(delivery)
    assert delivery.status == "dead_letter"
    assert delivery.attempt_count == webhook_service.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_replay_clones_into_pending(session, admin_user):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://x.example",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {"organization_id": admin_user.organization_id, "data": {"experiment_id": 1}},
    )
    original = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id)
        )
    ).scalar_one()
    # Mark the original as dead_letter so we can prove replay clones a fresh row.
    original.status = "dead_letter"
    await session.commit()

    clone = await webhook_service.replay_delivery(session, original.id, admin_user.id)
    await session.commit()
    assert clone.id != original.id
    assert clone.status == "pending"
    assert clone.attempt_count == 0
    assert clone.payload_json == original.payload_json


@pytest.mark.asyncio
async def test_fire_test_event(session, admin_user):
    sub, _ = await webhook_service.create_subscription(
        session,
        org_id=admin_user.organization_id,
        name="A",
        url="https://x.example",
        events=[WEBHOOK_EXPERIMENT_CREATED],
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    delivery = await webhook_service.fire_test_event(
        session, sub.id, admin_user.organization_id, admin_user.id
    )
    await session.commit()
    assert delivery.event_type == webhook_service.TEST_EVENT_TYPE
    assert delivery.status == "pending"


@pytest.mark.asyncio
async def test_dispatcher_subscribes_via_event_bus(session, admin_user):
    """When the integration handler emits INTEGRATION_EXPERIMENT_CREATED,
    delivery rows materialize through the event_bus path (not by direct call)."""
    # Avoid duplicate subscriptions across tests by saving/restoring state.
    original = dict(event_bus._subscribers)
    try:
        event_bus._subscribers.clear()
        webhook_dispatcher.subscribe_all()
        await webhook_service.create_subscription(
            session,
            org_id=admin_user.organization_id,
            name="A",
            url="https://x.example",
            events=[WEBHOOK_EXPERIMENT_CREATED],
            created_by_user_id=admin_user.id,
        )
        await session.commit()

        await event_bus.emit(
            INTEGRATION_EXPERIMENT_CREATED,
            {
                "organization_id": admin_user.organization_id,
                "data": {"experiment_id": 7},
            },
        )

        # Give the event bus a tick to deliver
        await asyncio.sleep(0.05)
        deliveries = (
            await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.event_type == WEBHOOK_EXPERIMENT_CREATED
                )
            )
        ).scalars().all()
        assert len(deliveries) == 1
    finally:
        event_bus._subscribers = original
