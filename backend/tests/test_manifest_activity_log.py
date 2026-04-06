"""Tests for Chunk 6: Manifest activity logging."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_feed import ActivityFeedEntry
from app.services.manifest_activity_service import (
    log_batch_complete,
    log_file_failed,
    log_file_verified,
    log_manifest_detected,
)

pytestmark = pytest.mark.asyncio


async def _setup_org(session: AsyncSession) -> int:
    from app.models.organization import Organization
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Activity Log Org", setup_complete=True)
    session.add(org)
    await session.flush()
    await seed_builtin_roles(session, org.id)
    await session.commit()
    return org.id


async def test_manifest_detected_creates_activity(session):
    org_id = await _setup_org(session)
    await log_manifest_detected(session, org_id, "SEQ-001", 4)
    await session.commit()

    result = await session.execute(select(ActivityFeedEntry).where(ActivityFeedEntry.organization_id == org_id))
    entries = result.scalars().all()
    assert len(entries) >= 1
    assert "SEQ-001" in entries[0].summary
    assert "4 files" in entries[0].summary


async def test_file_verified_creates_activity(session):
    org_id = await _setup_org(session)
    await log_file_verified(session, org_id, "SEQ-001", "sample1.fastq.gz", "Project A", "Experiment 1", "Sample 1")
    await session.commit()

    result = await session.execute(select(ActivityFeedEntry).where(ActivityFeedEntry.organization_id == org_id))
    entries = result.scalars().all()
    assert len(entries) >= 1
    assert "sample1.fastq.gz" in entries[0].summary


async def test_batch_complete_creates_activity(session):
    org_id = await _setup_org(session)
    await log_batch_complete(session, org_id, "SEQ-001", 4)
    await session.commit()

    result = await session.execute(select(ActivityFeedEntry).where(ActivityFeedEntry.organization_id == org_id))
    entries = result.scalars().all()
    assert len(entries) >= 1
    assert "complete" in entries[0].summary.lower()


async def test_file_failed_creates_warning_activity(session):
    org_id = await _setup_org(session)
    await log_file_failed(session, org_id, "SEQ-001", "bad_file.fastq.gz", 48)
    await session.commit()

    result = await session.execute(select(ActivityFeedEntry).where(ActivityFeedEntry.organization_id == org_id))
    entries = result.scalars().all()
    assert len(entries) >= 1
    assert "failed" in entries[0].summary.lower()
    # Severity stored in metadata_json
    assert entries[0].metadata_json.get("severity") == "warning"
