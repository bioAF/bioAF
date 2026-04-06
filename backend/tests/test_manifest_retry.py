"""Tests for Chunk 5: Manifest retry mechanism."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.manifest_retry_service import process_manifest_retries

pytestmark = pytest.mark.asyncio


async def _setup_batch_with_entries(session: AsyncSession) -> tuple[int, list[int]]:
    """Create a sequencing batch with pending manifest entries."""
    from app.models.organization import Organization
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Retry Test Org", setup_complete=True)
    session.add(org)
    await session.flush()
    await seed_builtin_roles(session, org.id)

    batch = SequencingBatch(
        organization_id=org.id,
        name="Retry Batch",
        code="SEQ-RETRY-001",
        status="ingesting",
        expected_file_count=3,
    )
    session.add(batch)
    await session.flush()

    entries = []
    for i, fname in enumerate(["file1.fastq.gz", "file2.fastq.gz", "file3.fastq.gz"]):
        entry = ManifestEntry(
            sequencing_batch_id=batch.id,
            expected_filename=fname,
            expected_md5=f"md5hash{i}",
            status="pending",
            retry_count=0,
        )
        session.add(entry)
        entries.append(entry)
    await session.flush()
    await session.commit()
    return batch.id, [e.id for e in entries]


async def test_pending_entries_get_retry_count_incremented(session):
    """Pending entries with retries remaining should have retry_count incremented."""
    batch_id, entry_ids = await _setup_batch_with_entries(session)

    await process_manifest_retries(session, max_retries=48)
    await session.commit()

    result = await session.execute(select(ManifestEntry).where(ManifestEntry.id == entry_ids[0]))
    entry = result.scalar_one()
    assert entry.retry_count == 1
    assert entry.last_check_at is not None


async def test_entries_exceeding_max_retries_marked_failed(session):
    """Entries at max retries should be marked 'failed'."""
    batch_id, entry_ids = await _setup_batch_with_entries(session)

    # Set entries to max retries
    for eid in entry_ids:
        await session.execute(ManifestEntry.__table__.update().where(ManifestEntry.id == eid).values(retry_count=47))
    await session.commit()

    await process_manifest_retries(session, max_retries=48)
    await session.commit()

    for eid in entry_ids:
        result = await session.execute(select(ManifestEntry).where(ManifestEntry.id == eid))
        entry = result.scalar_one()
        assert entry.status == "failed"
        assert entry.retry_count == 48


async def test_batch_status_updates_to_partial_complete(session):
    """Batch should become partial_complete when some entries fail and some are verified."""
    batch_id, entry_ids = await _setup_batch_with_entries(session)

    # Mark first entry as verified, set others to max retries
    await session.execute(
        ManifestEntry.__table__.update().where(ManifestEntry.id == entry_ids[0]).values(status="verified")
    )
    for eid in entry_ids[1:]:
        await session.execute(ManifestEntry.__table__.update().where(ManifestEntry.id == eid).values(retry_count=47))
    await session.commit()

    await process_manifest_retries(session, max_retries=48)
    await session.commit()

    result = await session.execute(select(SequencingBatch).where(SequencingBatch.id == batch_id))
    batch = result.scalar_one()
    assert batch.status == "partial_complete"


async def test_batch_status_complete_when_all_verified(session):
    """Batch should become complete when all entries are verified."""
    batch_id, entry_ids = await _setup_batch_with_entries(session)

    # Mark all entries as verified
    for eid in entry_ids:
        await session.execute(ManifestEntry.__table__.update().where(ManifestEntry.id == eid).values(status="verified"))
    await session.commit()

    await process_manifest_retries(session, max_retries=48)
    await session.commit()

    result = await session.execute(select(SequencingBatch).where(SequencingBatch.id == batch_id))
    batch = result.scalar_one()
    assert batch.status == "complete"
