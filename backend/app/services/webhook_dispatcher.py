"""Subscribes to internal events and persists webhook_deliveries rows for
every active subscription whose event_types list matches (ADR-051).

Internal event names are translated to public ones in one place here, so
service code can keep emitting its existing event constants without knowing
about the public vocabulary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import webhook_service
from app.services.event_bus import event_bus
from app.services.event_types import (
    EXPERIMENT_STATUS_CHANGED,
    INTEGRATION_EXPERIMENT_CREATED,
    INTEGRATION_EXPERIMENT_UPDATED,
    INTEGRATION_FILE_READY,
    INTEGRATION_FILE_REGISTERED,
    INTEGRATION_SAMPLE_CREATED,
    INTEGRATION_SAMPLE_QC_CHANGED,
    INTEGRATION_SAMPLE_UPDATED,
    WEBHOOK_EXPERIMENT_CREATED,
    WEBHOOK_EXPERIMENT_STATUS_CHANGED,
    WEBHOOK_EXPERIMENT_UPDATED,
    WEBHOOK_FILE_READY,
    WEBHOOK_FILE_REGISTERED,
    WEBHOOK_SAMPLE_CREATED,
    WEBHOOK_SAMPLE_QC_CHANGED,
    WEBHOOK_SAMPLE_UPDATED,
)

logger = logging.getLogger("bioaf.webhook_dispatcher")

# Internal event name -> public webhook event name.
_INTERNAL_TO_PUBLIC: dict[str, str] = {
    INTEGRATION_EXPERIMENT_CREATED: WEBHOOK_EXPERIMENT_CREATED,
    INTEGRATION_EXPERIMENT_UPDATED: WEBHOOK_EXPERIMENT_UPDATED,
    EXPERIMENT_STATUS_CHANGED: WEBHOOK_EXPERIMENT_STATUS_CHANGED,
    INTEGRATION_SAMPLE_CREATED: WEBHOOK_SAMPLE_CREATED,
    INTEGRATION_SAMPLE_UPDATED: WEBHOOK_SAMPLE_UPDATED,
    INTEGRATION_SAMPLE_QC_CHANGED: WEBHOOK_SAMPLE_QC_CHANGED,
    INTEGRATION_FILE_REGISTERED: WEBHOOK_FILE_REGISTERED,
    INTEGRATION_FILE_READY: WEBHOOK_FILE_READY,
}


async def dispatch_event(internal_event: str, payload: dict[str, Any]) -> None:
    """Translate, then insert webhook_deliveries rows for every matching
    active subscription in the originating org."""
    public_event = _INTERNAL_TO_PUBLIC.get(internal_event)
    if public_event is None:
        return
    org_id = payload.get("organization_id")
    if org_id is None:
        return

    from app import database as database_module

    envelope = {
        "id": webhook_service.envelope_id(),
        "event": public_event,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": org_id,
        "data": payload.get("data", {}),
    }

    async with database_module.async_session_factory() as session:
        subs = (
            (
                await session.execute(
                    select(WebhookSubscription).where(
                        WebhookSubscription.organization_id == org_id,
                        WebhookSubscription.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        created = 0
        for sub in subs:
            if public_event not in (sub.events or []):
                continue
            delivery = WebhookDelivery(
                subscription_id=sub.id,
                event_id=envelope["id"],
                event_type=public_event,
                payload_json=envelope,
                status="pending",
                next_attempt_at=datetime.now(timezone.utc),
            )
            session.add(delivery)
            created += 1
        if created:
            await session.commit()


def subscribe_all() -> None:
    """Wire the dispatcher into the event bus. Called from app/main.py
    lifespan startup."""
    for internal_event in _INTERNAL_TO_PUBLIC:
        event_bus.subscribe(internal_event, _make_callback(internal_event))


def _make_callback(internal_event: str):
    async def _cb(payload: dict[str, Any]) -> None:
        try:
            await dispatch_event(internal_event, payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("webhook dispatcher failed for %s: %s", internal_event, exc)

    return _cb
