"""Cross-cutting acceptance for webhook production (01C).

- Transactions: nothing is delivered before the outer commit; a released savepoint alone publishes
  nothing; rollback and a failed commit publish nothing; a session reused after a rollback does not
  leak the discarded event into the next transaction.
- Snapshots: a persisted delivery keeps its body through retries and Replay after the records change.
- The auto-ingest gate stays effective: both blocked entry points refuse without creating files,
  verification work, or events.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import select, text

from app.models.experiment import Experiment
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.sample import Sample
from app.models.webhook import WebhookDelivery
from app.schemas.sample import SampleCreate
from app.services import webhook_service, webhook_worker
from app.services.sample_service import SampleService
from tests.support.webhook_capture import WebhookCapture

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def capture(session, admin_user):
    c = await WebhookCapture.start(session, admin_user)
    yield c
    c.close()


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(organization_id=admin_user.organization_id, name="E", external_id="EXP-1", status="registered")
    session.add(exp)
    await session.commit()
    return exp


async def test_nothing_is_delivered_before_the_outer_commit(session, admin_user, experiment, capture):
    exp_id, user_id = experiment.id, admin_user.id
    async with session.begin_nested():
        await SampleService.create_sample(session, exp_id, user_id, SampleCreate(external_id="A"))
    # The savepoint was released, but the outer transaction has not committed.
    assert await capture.deliveries() == []
    await session.commit()
    assert [d["external_id"] for d in await capture.data("sample.created")] == ["A"]


async def test_a_rolled_back_savepoint_publishes_nothing_while_its_sibling_does(
    session, admin_user, experiment, capture
):
    exp_id, user_id = experiment.id, admin_user.id
    async with session.begin_nested():
        await SampleService.create_sample(session, exp_id, user_id, SampleCreate(external_id="KEPT"))
    savepoint = await session.begin_nested()
    await SampleService.create_sample(session, exp_id, user_id, SampleCreate(external_id="DROPPED"))
    await savepoint.rollback()
    await session.commit()
    assert [d["external_id"] for d in await capture.data("sample.created")] == ["KEPT"]


async def test_a_failed_commit_and_a_reused_session_publish_nothing_stale(session, admin_user, experiment, capture):
    exp_id, user_id = experiment.id, admin_user.id
    await SampleService.create_sample(session, exp_id, user_id, SampleCreate(external_id="DOOMED"))
    session.add(Experiment(organization_id=admin_user.organization_id, name=None, status="registered"))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()
    assert await capture.deliveries() == []

    # The same session, a later transaction: only its own event appears.
    await SampleService.create_sample(session, exp_id, user_id, SampleCreate(external_id="LATER"))
    await session.commit()
    assert [d["external_id"] for d in await capture.data("sample.created")] == ["LATER"]


async def test_a_delivery_keeps_its_snapshot_through_retry_and_replay(session, admin_user, experiment, capture):
    sample = await SampleService.create_sample(session, experiment.id, admin_user.id, SampleCreate(external_id="OLD"))
    await session.commit()
    assert len(await capture.deliveries()) == 1  # dispatched after commit
    [original] = (await session.execute(select(WebhookDelivery))).scalars().all()
    body = dict(original.payload_json)

    # The records change after emission.
    s = await session.get(Sample, sample.id)
    s.external_id = "NEW"
    exp = await session.get(Experiment, experiment.id)
    exp.external_id = "EXP-RELABELED"
    await session.commit()

    with respx.mock:
        respx.post("https://hooks.example/capture").mock(return_value=httpx.Response(500))
        await webhook_worker._drain_once()
    original.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    with respx.mock:
        route = respx.post("https://hooks.example/capture").mock(return_value=httpx.Response(200))
        await webhook_worker._drain_once()
    assert route.calls.last.request.content.decode().count('"OLD"') == 1

    clone = await webhook_service.replay_delivery(session, original.id, admin_user.id)
    await session.commit()
    await session.refresh(original)
    assert original.payload_json == body
    assert clone.payload_json == body and clone.event_id == original.event_id
    assert body["data"]["external_id"] == "OLD" and body["data"]["experiment_external_id"] == "EXP-1"


async def test_the_auto_ingest_gate_still_refuses_both_entry_points(session, admin_user, capture):
    from app.services.auto_ingest_gate import AutoIngestDisabledError
    from app.services.ingest_service import process_ingest_event
    from app.services.manifest_ingest_service import process_manifest_ingest

    with pytest.raises(AutoIngestDisabledError):
        await process_ingest_event(
            filename="S1_L001_R1_001.fastq.gz",
            source_bucket="bioaf-ingest-t",
            source_path="drop/S1_L001_R1_001.fastq.gz",
            org_id=admin_user.organization_id,
            db=session,
            content_md5="a" * 32,
        )
    with pytest.raises(AutoIngestDisabledError):
        await process_manifest_ingest(
            "filename,md5\nS1_L001_R1_001.fastq.gz," + "a" * 32 + "\n",
            "csv",
            admin_user.organization_id,
            "bioaf-ingest-t",
            session,
        )
    await session.commit()
    assert (await session.execute(select(File))).scalars().all() == []
    assert (await session.execute(select(FileVerification))).scalars().all() == []
    assert await capture.deliveries() == []
    count = (await session.execute(text("SELECT count(*) FROM ingest_events"))).scalar_one()
    assert count == 0
