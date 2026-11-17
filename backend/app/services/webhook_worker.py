"""Background worker for delivering pending webhook rows (ADR-051).

Polls webhook_deliveries.status='pending' with FOR UPDATE SKIP LOCKED, posts
the HMAC-signed body through the enforcing transport (webhook_transport), and
applies exponential backoff on failure. Dead-letters after MAX_ATTEMPTS. A
destination the policy refuses fails at once, with no request made. Deliveries
record safe codes (webhook_transport), never receiver bodies or raw errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app import runtime
from app.services import webhook_service, webhook_transport
from app.tasks.periodic import wait_or_stop

logger = logging.getLogger("bioaf.webhook_worker")

POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 50


async def _send_one(
    delivery: WebhookDelivery,
    subscription: WebhookSubscription,
) -> webhook_transport.DeliveryOutcome:
    """One authorized POST attempt through the enforcing transport (the only outbound path)."""
    body_bytes = json.dumps(delivery.payload_json, sort_keys=True).encode()
    signature = webhook_service.sign_payload(subscription.secret, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-bioAF-Event": delivery.event_type,
        "X-bioAF-Delivery": str(delivery.id),
        "X-bioAF-Signature": signature,
    }
    outcome = await webhook_transport.post_webhook(subscription.url, subscription.organization_id, body_bytes, headers)
    if not outcome.delivered:
        # IDs and a safe code only: never the URL, the receiver's body, or raw upstream errors.
        logger.warning(
            "webhook delivery %s (subscription %s) attempt failed: %s",
            delivery.id,
            subscription.id,
            outcome.code,
        )
    return outcome


async def _drain_once() -> int:
    """Try to deliver up to BATCH_SIZE pending rows. Returns count processed."""
    from app import database as database_module

    now = datetime.now(timezone.utc)
    processed = 0

    async with database_module.async_session_factory() as session:
        stmt = (
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        deliveries = (await session.execute(stmt)).scalars().all()
        if not deliveries:
            return 0

        sub_ids = {d.subscription_id for d in deliveries}
        subs = (
            (await session.execute(select(WebhookSubscription).where(WebhookSubscription.id.in_(sub_ids))))
            .scalars()
            .all()
        )
        subs_by_id = {s.id: s for s in subs}

        for delivery in deliveries:
            sub = subs_by_id.get(delivery.subscription_id)
            if sub is None or not sub.is_active:
                # Subscription gone or disabled: mark delivered to avoid
                # spinning. Operators can review the audit log.
                delivery.status = "failed"
                delivery.last_attempted_at = datetime.now(timezone.utc)
                delivery.last_response_status = None
                delivery.last_response_body = "subscription_inactive_or_missing"
                continue
            outcome = await _send_one(delivery, sub)
            delivery.last_attempted_at = datetime.now(timezone.utc)
            delivery.last_response_status = outcome.status_code
            delivery.last_response_body = outcome.code
            delivery.attempt_count = (delivery.attempt_count or 0) + 1
            if outcome.delivered:
                delivery.status = "delivered"
                delivery.delivered_at = datetime.now(timezone.utc)
                delivery.next_attempt_at = None
            elif outcome.terminal:
                # A malformed or denied destination: no request was made, and no retry can
                # change that until an administrator corrects the subscription and replays.
                delivery.status = "failed"
                delivery.next_attempt_at = None
            elif delivery.attempt_count >= webhook_service.MAX_ATTEMPTS:
                delivery.status = "dead_letter"
                delivery.next_attempt_at = None
            else:
                backoff_index = min(
                    delivery.attempt_count - 1,
                    len(webhook_service.BACKOFF_SECONDS) - 1,
                )
                delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                    seconds=webhook_service.BACKOFF_SECONDS[backoff_index]
                )
            processed += 1
        await session.commit()
    return processed


async def run_worker_loop(stop_event: asyncio.Event | None = None) -> None:
    """Long-running poll loop. Cancel-friendly; honors stop_event if given."""
    logger.info("webhook worker started")
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        owner = runtime.current_owner()
        # Paused (a restore review): delivery rows would be read from and written to the review copy.
        if owner is None or owner.paused is None:
            try:
                with owner.activity("webhook-worker") if owner is not None else nullcontext():
                    await _drain_once()
            except Exception:
                logger.exception("webhook worker iteration failed")
        if await wait_or_stop(POLL_INTERVAL_SECONDS, stop_event):
            break
    logger.info("webhook worker stopped")
