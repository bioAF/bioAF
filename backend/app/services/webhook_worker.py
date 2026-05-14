"""Background worker for delivering pending webhook rows (ADR-051).

Polls webhook_deliveries.status='pending' with FOR UPDATE SKIP LOCKED, posts
to the subscriber URL with HMAC-signed body, and applies exponential backoff
on failure. Dead-letters after MAX_ATTEMPTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import webhook_service

logger = logging.getLogger("bioaf.webhook_worker")

POLL_INTERVAL_SECONDS = 5
BATCH_SIZE = 50
REQUEST_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BODY_BYTES = 4096


async def _send_one(
    client: httpx.AsyncClient,
    delivery: WebhookDelivery,
    subscription: WebhookSubscription,
) -> tuple[bool, int | None, str | None]:
    body_bytes = json.dumps(delivery.payload_json, sort_keys=True).encode()
    signature = webhook_service.sign_payload(subscription.secret, body_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-bioAF-Event": delivery.event_type,
        "X-bioAF-Delivery": str(delivery.id),
        "X-bioAF-Signature": signature,
    }
    try:
        resp = await client.post(subscription.url, content=body_bytes, headers=headers)
        body_truncated = resp.text[:MAX_RESPONSE_BODY_BYTES]
        return (200 <= resp.status_code < 300, resp.status_code, body_truncated)
    except Exception as exc:
        logger.warning("webhook POST failed: %s", exc)
        return (False, None, str(exc)[:MAX_RESPONSE_BODY_BYTES])


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

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
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
                ok, status_code, body = await _send_one(client, delivery, sub)
                delivery.last_attempted_at = datetime.now(timezone.utc)
                delivery.last_response_status = status_code
                delivery.last_response_body = body
                delivery.attempt_count = (delivery.attempt_count or 0) + 1
                if ok:
                    delivery.status = "delivered"
                    delivery.delivered_at = datetime.now(timezone.utc)
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
        try:
            await _drain_once()
        except Exception:
            logger.exception("webhook worker iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    logger.info("webhook worker stopped")
