"""08E: ingest notifications follow the commit of the ingest records they describe.

Auto-ingest is gated off in production (``AUTO_INGEST_DISABLED``) and stays so. These are controlled
tests: each opens the gate for itself only, drives ``process_ingest_event`` on a session it owns, and
checks that UNMATCHED_FILE and DUPLICATE_FILE reach a subscriber only once their file and ingest event
rows are committed, and never after a rollback.

The redesign removed filename matching (``match_filename`` now raises), so the gated body cannot run
end to end; the tests stand in a matcher that reports "unmatched". FILES_CATALOGED and
UNCLAIMED_ENTITY need a real profile match, which the redesign has not rebuilt; they use the same
deferral and are not driven here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.storage.s3 import S3StorageProvider
from app.models.file import File
from app.models.ingest_event import IngestEvent
from app.services import auto_ingest_gate, ingest_service
from app.services.event_types import DUPLICATE_FILE, UNMATCHED_FILE
from app.services.ingest_service import process_ingest_event
from tests.support.committed_view import CommittedView

pytestmark = pytest.mark.asyncio

MD5 = "b" * 32


@pytest.fixture(autouse=True)
def open_gate(monkeypatch):
    """This test only: the production gate stays closed (see test_webhook_production_gates)."""
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setattr(auto_ingest_gate, "AUTO_INGEST_DISABLED", False)
    monkeypatch.setattr(
        ingest_service,
        "match_filename",
        lambda filename, profiles: SimpleNamespace(status="unmatched", parse_result=None),
    )
    adapter = S3StorageProvider(org_slug="t")
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: adapter)


async def _committed(s, payload):
    file = await s.get(File, payload["entity_id"])
    if file is None:
        return None
    ingest_events = (await s.execute(select(IngestEvent.ingest_status).where(IngestEvent.file_id == file.id))).all()
    return sorted(status for (status,) in ingest_events)


@pytest_asyncio.fixture
async def ingests():
    view = CommittedView([UNMATCHED_FILE, DUPLICATE_FILE], read=_committed)
    yield view
    view.close()


async def _ingest(session, admin_user, name: str, md5: str | None = None):
    return await process_ingest_event(
        filename=name,
        source_bucket="bioaf-ingest-t",
        source_path=f"drop/{name}",
        org_id=admin_user.organization_id,
        db=session,
        user_id=admin_user.id,
        content_md5=md5,
    )


async def test_an_unmatched_file_is_announced_once_its_records_commit(session, admin_user, ingests):
    await _ingest(session, admin_user, "mystery.fastq.gz")
    await ingests.settle()
    assert ingests.events == []

    await session.commit()
    await ingests.settle()

    [(payload, committed)] = ingests.seen(UNMATCHED_FILE)
    assert committed == ["unmatched"]
    assert payload["org_id"] == admin_user.organization_id


async def test_a_duplicate_is_announced_once_its_ingest_record_commits(session, admin_user, ingests):
    await _ingest(session, admin_user, "first.fastq.gz", MD5)
    await session.commit()
    await ingests.settle()

    await _ingest(session, admin_user, "again.fastq.gz", MD5)
    await ingests.settle()
    assert ingests.seen(DUPLICATE_FILE) == []

    await session.commit()
    await ingests.settle()
    [(payload, committed)] = ingests.seen(DUPLICATE_FILE)
    assert committed == ["duplicate", "unmatched"]  # the original's ingest plus the duplicate's
    assert payload["org_id"] == admin_user.organization_id


async def test_a_rolled_back_ingest_announces_nothing(session, admin_user, ingests):
    await _ingest(session, admin_user, "first.fastq.gz", MD5)
    await session.commit()
    await ingests.settle()
    before = list(ingests.events)

    await _ingest(session, admin_user, "again.fastq.gz", MD5)
    await _ingest(session, admin_user, "other.fastq.gz")
    await session.rollback()
    await ingests.settle()

    assert ingests.events == before
