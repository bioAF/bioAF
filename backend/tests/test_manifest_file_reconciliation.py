"""Tests for ManifestEntry reconciliation when files arrive via ingest."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.ingest_service import process_ingest_event


async def _setup_batch_and_entries(
    session: AsyncSession,
    org_id: int,
    batch_code: str = "REC-BATCH",
    entries: list[tuple[str, str]] | None = None,
) -> tuple[int, list[int]]:
    """Create a sequencing batch with manifest entries. Returns (batch_id, entry_ids)."""
    batch = SequencingBatch(
        organization_id=org_id,
        code=batch_code,
        name=batch_code,
        status="ingesting",
        expected_file_count=len(entries or []),
    )
    session.add(batch)
    await session.flush()

    entry_ids = []
    for filename, md5 in entries or []:
        entry = ManifestEntry(
            sequencing_batch_id=batch.id,
            expected_filename=filename,
            expected_md5=md5,
            status="pending",
        )
        session.add(entry)
        await session.flush()
        entry_ids.append(entry.id)

    return batch.id, entry_ids


@pytest.mark.asyncio
async def test_file_arrives_after_manifest(client: AsyncClient, admin_token: str, session: AsyncSession):
    """File arriving after manifest should verify the ManifestEntry and link the file."""
    # Get org_id
    proj = await client.post(
        "/api/projects",
        json={"name": "Reconcile Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    org_row = (
        await session.execute(
            text("SELECT organization_id FROM projects WHERE id = :pid"),
            {"pid": proj.json()["id"]},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    # Create manifest entry
    batch_id, entry_ids = await _setup_batch_and_entries(
        session,
        org_id,
        entries=[("sample_S1_L001_R1_001.fastq.gz", "abc123def456")],
    )
    await session.flush()

    # Process file ingest event
    await process_ingest_event(
        filename="sample_S1_L001_R1_001.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="delivery/sample_S1_L001_R1_001.fastq.gz",
        org_id=org_id,
        db=session,
        content_md5="abc123def456",
        ingest_source="simulate",
    )

    # Check manifest entry was updated
    row = (
        await session.execute(
            text("SELECT status, file_id FROM manifest_entries WHERE id = :eid"),
            {"eid": entry_ids[0]},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "verified"
    assert row[1] is not None  # file_id linked


@pytest.mark.asyncio
async def test_md5_mismatch(client: AsyncClient, admin_token: str, session: AsyncSession):
    """File with wrong MD5 should set ManifestEntry to checksum_mismatch."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Mismatch Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    org_row = (
        await session.execute(
            text("SELECT organization_id FROM projects WHERE id = :pid"),
            {"pid": proj.json()["id"]},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    batch_id, entry_ids = await _setup_batch_and_entries(
        session,
        org_id,
        batch_code="MISMATCH-BATCH",
        entries=[("sample.fastq.gz", "expected_hash")],
    )
    await session.flush()

    await process_ingest_event(
        filename="sample.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="delivery/sample.fastq.gz",
        org_id=org_id,
        db=session,
        content_md5="wrong_hash",
        ingest_source="simulate",
    )

    row = (
        await session.execute(
            text("SELECT status, error_message FROM manifest_entries WHERE id = :eid"),
            {"eid": entry_ids[0]},
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "checksum_mismatch"
    assert "wrong_hash" in (row[1] or "")


@pytest.mark.asyncio
async def test_file_without_manifest_entry(client: AsyncClient, admin_token: str, session: AsyncSession):
    """File with no matching ManifestEntry should proceed with normal ingest."""
    proj = await client.post(
        "/api/projects",
        json={"name": "No Entry Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    org_row = (
        await session.execute(
            text("SELECT organization_id FROM projects WHERE id = :pid"),
            {"pid": proj.json()["id"]},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    # No manifest entries exist
    event = await process_ingest_event(
        filename="orphan_file.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="delivery/orphan_file.fastq.gz",
        org_id=org_id,
        db=session,
        content_md5="somehash",
        ingest_source="simulate",
    )

    # Should succeed without error
    assert event is not None
    assert event.file_id is not None


@pytest.mark.asyncio
async def test_manifest_entry_enhances_resolution(client: AsyncClient, admin_token: str, session: AsyncSession):
    """ManifestEntry's resolved_sample_id should enhance the ingest event."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Enhance Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Enhance Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "ENHANCE-001", "sequencing_batch_code": "ENH-BATCH"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await session.commit()
    sample_id = sample_resp.json()["id"]

    org_row = (
        await session.execute(
            text("SELECT organization_id FROM experiments WHERE id = :eid"),
            {"eid": exp_id},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    # Create a manifest entry with resolved sample
    batch_row = (
        await session.execute(
            text("SELECT id FROM sequencing_batches WHERE code = 'ENH-BATCH' AND organization_id = :org"),
            {"org": org_id},
        )
    ).fetchone()
    assert batch_row is not None

    entry = ManifestEntry(
        sequencing_batch_id=batch_row[0],
        expected_filename="enhance_file.fastq.gz",
        expected_md5="enhash",
        resolved_sample_id=sample_id,
        resolved_experiment_id=exp_id,
        status="pending",
    )
    session.add(entry)
    await session.flush()

    # Ingest the file (no naming profile match expected)
    event = await process_ingest_event(
        filename="enhance_file.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="delivery/enhance_file.fastq.gz",
        org_id=org_id,
        db=session,
        content_md5="enhash",
        ingest_source="simulate",
    )

    # Ingest event should pick up the resolution from the manifest entry
    assert event.resolved_sample_id == sample_id
    assert event.resolved_experiment_id == exp_id
