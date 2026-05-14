"""Webhook subscription management plus HMAC signing helpers (ADR-051)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import audit_service
from app.services.event_types import ALL_WEBHOOK_EVENT_TYPES

TEST_EVENT_TYPE = "webhook.test"
VALID_EVENT_TYPES: frozenset[str] = frozenset(list(ALL_WEBHOOK_EVENT_TYPES) + [TEST_EVENT_TYPE])

BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 1800, 7200, 43200)
MAX_ATTEMPTS = 5


def validate_event_types(events: list[str]) -> list[str]:
    unknown = [e for e in events if e not in VALID_EVENT_TYPES]
    if unknown:
        raise ValueError(f"Unknown event type(s): {', '.join(sorted(unknown))}")
    return events


def _generate_secret() -> str:
    return f"whsec_{secrets.token_urlsafe(32)}"


def sign_payload(secret: str, body_bytes: bytes, timestamp: int | None = None) -> str:
    """Return the `t=<unix>,v1=<sha256hex>` header value for a payload."""
    t = timestamp if timestamp is not None else int(time.time())
    message = f"{t}.".encode() + body_bytes
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def envelope_id() -> str:
    """ULID-shaped id. Real ULIDs are time-sortable; this stand-in is uuid4
    base32 truncated, which still gives an opaque dedupe key."""
    return "evt_" + uuid.uuid4().hex[:24]


async def create_subscription(
    session: AsyncSession,
    org_id: int,
    name: str,
    url: str,
    events: list[str],
    created_by_user_id: int,
) -> tuple[WebhookSubscription, str]:
    """Create a new subscription. Returns (row, plaintext_secret) where the
    plaintext is shown to the admin exactly once at create time."""
    validate_event_types(events)
    secret = _generate_secret()
    row = WebhookSubscription(
        organization_id=org_id,
        name=name,
        url=url,
        secret=secret,
        events=events,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=created_by_user_id,
        entity_type="webhook_subscription",
        entity_id=row.id,
        action="create",
        details={"name": name, "url": url, "events": events},
    )
    return row, secret


async def list_subscriptions(session: AsyncSession, org_id: int) -> list[WebhookSubscription]:
    stmt = (
        select(WebhookSubscription)
        .where(WebhookSubscription.organization_id == org_id)
        .order_by(WebhookSubscription.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_subscription(
    session: AsyncSession,
    sub_id: int,
    org_id: int,
    actor_user_id: int,
    *,
    name: str | None = None,
    url: str | None = None,
    events: list[str] | None = None,
    is_active: bool | None = None,
) -> WebhookSubscription:
    row = (
        await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == sub_id,
                WebhookSubscription.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"webhook_subscription {sub_id} not found")
    updates: dict = {}
    if name is not None:
        row.name = name
        updates["name"] = name
    if url is not None:
        row.url = url
        updates["url"] = url
    if events is not None:
        validate_event_types(events)
        row.events = events
        updates["events"] = events
    if is_active is not None:
        row.is_active = is_active
        updates["is_active"] = is_active
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="webhook_subscription",
        entity_id=row.id,
        action="update",
        details=updates,
    )
    return row


async def rotate_secret(session: AsyncSession, sub_id: int, org_id: int, actor_user_id: int) -> tuple[WebhookSubscription, str]:
    row = (
        await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == sub_id,
                WebhookSubscription.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"webhook_subscription {sub_id} not found")
    new_secret = _generate_secret()
    row.secret = new_secret
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="webhook_subscription",
        entity_id=row.id,
        action="rotate_secret",
    )
    return row, new_secret


async def disable_subscription(session: AsyncSession, sub_id: int, org_id: int, actor_user_id: int) -> WebhookSubscription:
    return await update_subscription(
        session, sub_id, org_id, actor_user_id, is_active=False
    )


async def list_deliveries(
    session: AsyncSession,
    sub_id: int,
    status: str | None = None,
    limit: int = 50,
) -> list[WebhookDelivery]:
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.subscription_id == sub_id)
        .order_by(WebhookDelivery.id.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(WebhookDelivery.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def replay_delivery(
    session: AsyncSession, delivery_id: int, actor_user_id: int
) -> WebhookDelivery:
    """Clone an existing delivery into a fresh pending row."""
    from datetime import datetime, timezone

    row = (
        await session.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"webhook_delivery {delivery_id} not found")
    clone = WebhookDelivery(
        subscription_id=row.subscription_id,
        event_id=row.event_id,
        event_type=row.event_type,
        payload_json=row.payload_json,
        status="pending",
        attempt_count=0,
        next_attempt_at=datetime.now(timezone.utc),
    )
    session.add(clone)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="webhook_delivery",
        entity_id=clone.id,
        action="replay",
        details={"original_delivery_id": delivery_id},
    )
    return clone


async def fire_test_event(
    session: AsyncSession, sub_id: int, org_id: int, actor_user_id: int
) -> WebhookDelivery:
    """Synthesize a webhook.test event delivery for an admin-driven smoke test."""
    from datetime import datetime, timezone

    sub = (
        await session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == sub_id,
                WebhookSubscription.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise LookupError(f"webhook_subscription {sub_id} not found")
    payload = {
        "id": envelope_id(),
        "event": TEST_EVENT_TYPE,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": org_id,
        "data": {"sender": "bioaf-admin", "purpose": "subscription_test"},
    }
    delivery = WebhookDelivery(
        subscription_id=sub.id,
        event_id=payload["id"],
        event_type=TEST_EVENT_TYPE,
        payload_json=payload,
        status="pending",
        next_attempt_at=datetime.now(timezone.utc),
    )
    session.add(delivery)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="webhook_subscription",
        entity_id=sub.id,
        action="test_fired",
        details={"delivery_id": delivery.id},
    )
    return delivery
