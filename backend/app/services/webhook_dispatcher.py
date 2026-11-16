"""Subscribes to internal events and persists webhook_deliveries rows for
every active subscription whose event_types list matches (ADR-051).

Internal event names are translated to public ones in one place here, so
service code can keep emitting its existing event constants without knowing
about the public vocabulary.

A bus payload is ``{"organization_id": <int>, "data": {...}}``. The experiment status event is also
consumed by notifications, so its payload carries the legacy notification fields and ``org_id``; the
dispatcher adapts it. A payload naming two different organizations is refused, never resolved by
choosing one. ``data`` must match its public event's contract exactly
(app/services/webhook_events.py); nothing is looked up after commit to complete it.

Each dispatch returns a ``DispatchReceipt``: ``persisted`` (delivery rows committed),
``no_matching_subscriptions`` (a valid success), or ``failed`` with a safe code. A scoped binding
(``subscribe_all(session_factory=..., collect_receipts=True)``) lets a separate process such as the
``register-outputs`` CLI use its own database and check that every committed event was persisted
before it exits; the server's binding keeps looking up the application's current session factory on
every dispatch, so a database restore or swap still applies.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.services import webhook_events, webhook_service
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
INTERNAL_EVENTS = tuple(_INTERNAL_TO_PUBLIC)

_SUBJECT_KEYS = (("file_id", "file"), ("sample_id", "sample"), ("experiment_id", "experiment"))


@dataclass(frozen=True)
class DispatchReceipt:
    internal_event: str
    public_event: str | None
    organization_id: int | None
    subject: tuple[str, int] | None
    outcome: str  # persisted | no_matching_subscriptions | failed
    deliveries: int = 0
    code: str | None = None


def _subject(data: Any) -> tuple[str, int] | None:
    if not isinstance(data, dict):
        return None
    for key, kind in _SUBJECT_KEYS:
        if isinstance(data.get(key), int):
            return (kind, data[key])
    return None


def _organization(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    new, legacy = payload.get("organization_id"), payload.get("org_id")
    if new is not None and legacy is not None and new != legacy:
        return None, "organization_conflict"
    org = new if new is not None else legacy
    if org is None:
        return None, "missing_organization"
    if not isinstance(org, int) or isinstance(org, bool):
        return None, "invalid_organization"
    return org, None


def _default_factory():
    from app import database as database_module

    return database_module.async_session_factory


async def dispatch_event(
    internal_event: str,
    payload: dict[str, Any],
    *,
    session_factory: Callable[[], Any] | None = None,
) -> DispatchReceipt:
    """Translate and validate one event, then insert a delivery row for every matching active
    subscription in the originating organization (one shared event body and id)."""
    public_event = _INTERNAL_TO_PUBLIC.get(internal_event)
    data = payload.get("data") if isinstance(payload, dict) else None
    subject = _subject(data)
    if public_event is None:
        return DispatchReceipt(internal_event, None, None, subject, "failed", code="unmapped_event")
    org_id, problem = _organization(payload if isinstance(payload, dict) else {})
    if problem is not None:
        logger.warning("webhook event %s refused: %s (subject %s)", public_event, problem, subject)
        return DispatchReceipt(internal_event, public_event, None, subject, "failed", code=problem)
    try:
        webhook_events.validate_data(public_event, data)
    except webhook_events.InvalidEventData:
        logger.warning("webhook event %s refused: invalid_payload (subject %s)", public_event, subject)
        return DispatchReceipt(internal_event, public_event, org_id, subject, "failed", code="invalid_payload")

    envelope = {
        "id": webhook_service.envelope_id(),
        "event": public_event,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": org_id,
        "data": data,
    }
    factory = session_factory if session_factory is not None else _default_factory()
    async with factory() as session:
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
            session.add(
                WebhookDelivery(
                    subscription_id=sub.id,
                    event_id=envelope["id"],
                    event_type=public_event,
                    payload_json=envelope,
                    status="pending",
                    next_attempt_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        if created:
            await session.commit()
    outcome = "persisted" if created else "no_matching_subscriptions"
    return DispatchReceipt(internal_event, public_event, org_id, subject, outcome, deliveries=created)


@dataclass
class DispatcherBinding:
    """The callbacks one ``subscribe_all`` installed, removable exactly."""

    session_factory: Callable[[], Any] | None
    collect_receipts: bool
    callbacks: list[tuple[str, Callable]] = field(default_factory=list)
    receipts: list[DispatchReceipt] = field(default_factory=list)
    closed: bool = False

    def close(self) -> None:
        global _server_binding
        for event, callback in self.callbacks:
            event_bus.unsubscribe(event, callback)
        self.callbacks.clear()
        self.closed = True
        if _server_binding is self:
            _server_binding = None


_server_binding: DispatcherBinding | None = None


def subscribe_all(
    *, session_factory: Callable[[], Any] | None = None, collect_receipts: bool = False
) -> DispatcherBinding:
    """Wire the dispatcher into the event bus and return the binding.

    The default binding (app/main.py lifespan) is installed once per process; asking again returns
    it. A scoped binding (an explicit factory, receipts) is a separate, closable binding for one
    command."""
    global _server_binding
    scoped = session_factory is not None or collect_receipts
    if not scoped and _server_binding is not None and not _server_binding.closed:
        live = all(cb in event_bus._subscribers.get(event, []) for event, cb in _server_binding.callbacks)
        if live:
            return _server_binding
        _server_binding.close()  # its callbacks were cleared from the bus; bind afresh
    binding = DispatcherBinding(session_factory=session_factory, collect_receipts=collect_receipts)
    for internal_event in _INTERNAL_TO_PUBLIC:
        callback = _make_callback(internal_event, binding)
        event_bus.subscribe(internal_event, callback)
        binding.callbacks.append((internal_event, callback))
    if not scoped:
        _server_binding = binding
    return binding


def _make_callback(internal_event: str, binding: DispatcherBinding):
    async def _cb(payload: dict[str, Any]) -> None:
        try:
            receipt = await dispatch_event(internal_event, payload, session_factory=binding.session_factory)
        except Exception:
            logger.exception("webhook dispatcher failed for %s", internal_event)
            receipt = DispatchReceipt(
                internal_event,
                _INTERNAL_TO_PUBLIC.get(internal_event),
                None,
                _subject(payload.get("data") if isinstance(payload, dict) else None),
                "failed",
                code="persistence_failed",
            )
        if binding.collect_receipts:
            binding.receipts.append(receipt)

    return _cb
