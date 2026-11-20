"""08E: ingest notifications follow the commit of the ingest records they describe.

Auto-ingest is gated off in production (``AUTO_INGEST_DISABLED``) and stays so. These are controlled
tests: each opens the gate for itself only, drives ``process_ingest_event`` on a session it owns, and
checks that UNMATCHED_FILE and DUPLICATE_FILE reach a subscriber only once their file and ingest event
rows are committed, and never after a rollback.

The redesign removed filename matching and entity resolution (``match_filename`` and
``resolve_entities`` now raise), so the gated body cannot run end to end. The tests stand in a matcher
that reports "unmatched" by default, and, for FILES_CATALOGED and UNCLAIMED_ENTITY, a matched parse
whose project and experiment codes resolve to nothing, so the gated body auto-creates them.
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
from app.services.event_types import DUPLICATE_FILE, FILES_CATALOGED, UNCLAIMED_ENTITY, UNMATCHED_FILE
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


# --- matched files: cataloged, with auto-created (unclaimed) entities -----------------------------


async def _ingest_event(s, payload):
    event_id = payload["entity_id"]
    event = await s.get(IngestEvent, event_id)
    return None if event is None else (event.ingest_status, event.auto_created_entities)


@pytest_asyncio.fixture
async def matched(session, admin_user, monkeypatch):
    """A filename that parses against a profile, with project and experiment codes nobody has yet."""
    from app.schemas.naming_profile import NamingProfileCreate, SegmentDefinition
    from app.services.naming_profile_service import NamingProfileService

    profile = await NamingProfileService.create_profile(
        session,
        admin_user.organization_id,
        admin_user.id,
        NamingProfileCreate(
            name="CRO",
            segments=[
                SegmentDefinition(
                    position=0,
                    identifier="PRJ",
                    field_name="ProjectCode",
                    field_type="number",
                    padding=2,
                    date_format=None,
                    is_system_chip=True,
                )
            ],
        ),
    )
    await session.commit()
    parse = SimpleNamespace(
        profile_id=profile.id, segments={"project_code": "NEWPRJ", "experiment_code": "NEWEXP", "sample_id": None}
    )
    monkeypatch.setattr(
        ingest_service,
        "match_filename",
        lambda filename, profiles: SimpleNamespace(status="matched", parse_result=parse),
    )

    async def nothing_resolved(*args, **kwargs):
        return SimpleNamespace(project_id=None, experiment_id=None, sample_id=None)

    monkeypatch.setattr(ingest_service, "resolve_entities", nothing_resolved)


@pytest_asyncio.fixture
async def catalog():
    view = CommittedView([FILES_CATALOGED, UNCLAIMED_ENTITY], read=_ingest_event)
    yield view
    view.close()


async def test_a_cataloged_file_and_its_unclaimed_entities_are_announced_once_committed(
    session, admin_user, matched, catalog
):
    event = await _ingest(session, admin_user, "NEWPRJ_NEWEXP.fastq.gz")
    await catalog.settle()
    assert catalog.events == []

    await session.commit()
    await catalog.settle()

    [(cataloged, committed_cataloged)] = catalog.seen(FILES_CATALOGED)
    [(unclaimed, committed_unclaimed)] = catalog.seen(UNCLAIMED_ENTITY)
    assert cataloged["entity_id"] == unclaimed["entity_id"] == event.id
    assert committed_cataloged[0] == committed_unclaimed[0] == "cataloged"
    created = committed_unclaimed[1]
    assert len(created["projects"]) == 1 and len(created["experiments"]) == 1 and created["samples"] == []
    assert unclaimed["metadata"] == created
    assert {cataloged["org_id"], unclaimed["org_id"]} == {admin_user.organization_id}


async def test_a_rolled_back_cataloged_ingest_announces_nothing(session, admin_user, matched, catalog):
    await _ingest(session, admin_user, "NEWPRJ_NEWEXP.fastq.gz")
    await session.rollback()
    await catalog.settle()

    assert catalog.events == []


async def test_the_unclaimed_announcement_is_a_snapshot_of_what_was_created(session, admin_user, matched, catalog):
    """The payload must not share the ingest event's own dictionary: a later change to that object
    before commit (it is returned to the caller) must not rewrite what subscribers are told."""
    event = await _ingest(session, admin_user, "NEWPRJ_NEWEXP.fastq.gz")
    created_at_ingest = {k: list(v) for k, v in event.auto_created_entities.items()}
    event.auto_created_entities["projects"].append(999999)

    await session.commit()
    await catalog.settle()

    [(payload, (_status, committed))] = catalog.seen(UNCLAIMED_ENTITY)
    assert payload["metadata"] == created_at_ingest == committed
