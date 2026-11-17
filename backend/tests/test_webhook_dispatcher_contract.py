"""01C.1: the dispatcher persists only well-formed public events for their own organization.

The bus payload carries ``organization_id`` (or the legacy ``org_id`` of the status notification) and
``data`` with exactly the public event's fields. A payload naming two different organizations, or with
data that does not match its contract, is refused and diagnosed; nothing is looked up after commit to
fill a gap. Each dispatch returns a receipt saying what was persisted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.webhook import WebhookDelivery
from app.services import webhook_dispatcher, webhook_service
from app.services.event_bus import event_bus
from app.services.event_types import (
    EXPERIMENT_STATUS_CHANGED,
    INTEGRATION_EXPERIMENT_CREATED,
    INTEGRATION_FILE_REGISTERED,
    WEBHOOK_EXPERIMENT_CREATED,
    WEBHOOK_EXPERIMENT_STATUS_CHANGED,
    WEBHOOK_FILE_REGISTERED,
)

pytestmark = pytest.mark.asyncio

EXP = {"experiment_id": 7, "external_id": "LIMS-7", "project_id": 3, "project_external_id": None}


async def _subscribe(session, user, events):
    await webhook_service.create_subscription(
        session,
        org_id=user.organization_id,
        name="LIMS",
        url="https://hooks.example/h",
        events=events,
        created_by_user_id=user.id,
    )
    await session.commit()


async def _deliveries(session):
    return (await session.execute(select(WebhookDelivery))).scalars().all()


async def test_a_well_formed_event_is_persisted_with_a_receipt(session, admin_user):
    await _subscribe(session, admin_user, [WEBHOOK_EXPERIMENT_CREATED])
    receipt = await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": admin_user.organization_id, "data": EXP}
    )
    assert receipt.outcome == "persisted" and receipt.deliveries == 1
    assert receipt.public_event == WEBHOOK_EXPERIMENT_CREATED
    [row] = await _deliveries(session)
    assert row.payload_json["data"] == EXP


async def test_no_matching_subscription_is_a_valid_outcome(session, admin_user):
    receipt = await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": admin_user.organization_id, "data": EXP}
    )
    assert receipt.outcome == "no_matching_subscriptions" and receipt.deliveries == 0


@pytest.mark.parametrize(
    "data",
    [
        {"experiment_id": 7},  # missing identity fields (the original defect)
        {**EXP, "notes": "secret"},  # an extra field never leaks
        None,
        [],
    ],
)
async def test_data_that_breaks_its_contract_is_refused(session, admin_user, data):
    await _subscribe(session, admin_user, [WEBHOOK_EXPERIMENT_CREATED])
    receipt = await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED, {"organization_id": admin_user.organization_id, "data": data}
    )
    assert receipt.outcome == "failed" and receipt.code == "invalid_payload"
    assert await _deliveries(session) == []


async def test_file_samples_must_agree_with_sample_ids(session, admin_user):
    await _subscribe(session, admin_user, [WEBHOOK_FILE_REGISTERED])
    data = {
        "file_id": 1,
        "external_id": None,
        "filename": "a",
        "file_type": "fastq",
        "source_type": "upload",
        "project_id": None,
        "project_external_id": None,
        "experiment_id": None,
        "experiment_external_id": None,
        "sample_ids": [2],
        "samples": [],
    }
    receipt = await webhook_dispatcher.dispatch_event(
        INTEGRATION_FILE_REGISTERED, {"organization_id": admin_user.organization_id, "data": data}
    )
    assert receipt.outcome == "failed"


async def test_the_status_notification_payload_is_adapted_from_its_legacy_org_key(session, admin_user):
    await _subscribe(session, admin_user, [WEBHOOK_EXPERIMENT_STATUS_CHANGED])
    payload = {
        "event_type": EXPERIMENT_STATUS_CHANGED,
        "org_id": admin_user.organization_id,
        "user_id": admin_user.id,
        "entity_type": "experiment",
        "entity_id": 7,
        "title": "t",
        "message": "m",
        "metadata": {"old_status": "registered", "new_status": "library_prep"},
        "data": {**EXP, "old_status": "registered", "new_status": "library_prep"},
    }
    receipt = await webhook_dispatcher.dispatch_event(EXPERIMENT_STATUS_CHANGED, payload)
    assert receipt.outcome == "persisted"
    [row] = await _deliveries(session)
    assert row.payload_json["organization_id"] == admin_user.organization_id
    assert row.payload_json["data"]["old_status"] == "registered"
    assert "title" not in row.payload_json["data"]


async def test_conflicting_organizations_are_refused(session, admin_user):
    await _subscribe(session, admin_user, [WEBHOOK_EXPERIMENT_CREATED])
    receipt = await webhook_dispatcher.dispatch_event(
        INTEGRATION_EXPERIMENT_CREATED,
        {"organization_id": admin_user.organization_id, "org_id": admin_user.organization_id + 1, "data": EXP},
    )
    assert receipt.outcome == "failed" and receipt.code == "organization_conflict"
    assert await _deliveries(session) == []


async def test_a_payload_without_an_organization_is_refused(session, admin_user):
    receipt = await webhook_dispatcher.dispatch_event(INTEGRATION_EXPERIMENT_CREATED, {"data": EXP})
    assert receipt.outcome == "failed" and receipt.code == "missing_organization"


async def test_subscriptions_are_installed_once_and_removed_exactly():
    original = dict(event_bus._subscribers)
    try:
        event_bus._subscribers.clear()
        first = webhook_dispatcher.subscribe_all()
        again = webhook_dispatcher.subscribe_all()
        assert again is first
        assert all(len(event_bus._subscribers[e]) == 1 for e in webhook_dispatcher.INTERNAL_EVENTS)
        first.close()
        assert all(not event_bus._subscribers.get(e) for e in webhook_dispatcher.INTERNAL_EVENTS)
        # A fresh binding after close works again.
        webhook_dispatcher.subscribe_all().close()
    finally:
        event_bus._subscribers = original


async def test_a_scoped_binding_uses_its_own_factory_and_collects_receipts(session, admin_user, db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.post_commit_events import defer_event, wait_for_deferred_events

    await _subscribe(session, admin_user, [WEBHOOK_EXPERIMENT_CREATED])
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    used = []

    def counting():
        used.append(1)
        return factory()

    original = dict(event_bus._subscribers)
    try:
        event_bus._subscribers.clear()
        binding = webhook_dispatcher.subscribe_all(session_factory=counting, collect_receipts=True)
        defer_event(
            session, INTEGRATION_EXPERIMENT_CREATED, {"organization_id": admin_user.organization_id, "data": EXP}
        )
        await session.commit()
        await wait_for_deferred_events()
        binding.close()
    finally:
        event_bus._subscribers = original
    assert used
    [receipt] = binding.receipts
    assert receipt.outcome == "persisted" and receipt.subject == ("experiment", 7)


async def test_a_binding_whose_callbacks_were_cleared_is_replaced():
    original = dict(event_bus._subscribers)
    try:
        event_bus._subscribers.clear()
        stale = webhook_dispatcher.subscribe_all()
        event_bus._subscribers.clear()
        fresh = webhook_dispatcher.subscribe_all()
        assert fresh is not stale
        assert all(len(event_bus._subscribers[e]) == 1 for e in webhook_dispatcher.INTERNAL_EVENTS)
        fresh.close()
    finally:
        event_bus._subscribers = original
