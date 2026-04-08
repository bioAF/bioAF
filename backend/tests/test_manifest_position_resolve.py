"""Tests for manifest ingest resolving samples by batch position."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.manifest_ingest_service import process_manifest_ingest


async def _create_naming_profile(session: AsyncSession, org_id: int) -> int:
    """Create an Illumina-style naming profile with sample_index segment."""
    from app.models.naming_profile import NamingProfile

    profile = NamingProfile(
        organization_id=org_id,
        name="Illumina S-number",
        delimiter="_",
        strip_extension=True,
        segments_json=[
            {"position": 0, "field": "ignore", "required": True},
            {"position": 1, "field": "ignore", "required": True},
            {"position": 2, "field": "ignore", "required": True},
            {"position": 3, "field": "sample_index", "required": True},
            {"position": 4, "field": "ignore", "required": True},
            {"position": 5, "field": "ignore", "required": True},
            {"position": 6, "field": "ignore", "required": True},
        ],
        project_code_mappings={},
        experiment_code_mappings={},
        status="active",
        created_by=1,
    )
    session.add(profile)
    await session.flush()
    result = await session.execute(text("SELECT id FROM naming_profiles WHERE name = 'Illumina S-number'"))
    row = result.fetchone()
    assert row is not None
    profile_id = row[0]
    await session.flush()
    return profile_id


@pytest.mark.asyncio
async def test_manifest_resolves_by_sample_index(client: AsyncClient, admin_token: str, session: AsyncSession):
    """Manifest with S-number filenames resolves correct samples via batch position."""
    # Create project and experiment
    proj = await client.post(
        "/api/projects",
        json={"name": "Manifest Resolve Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Manifest Resolve Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    # Create 2 samples with positions 1 and 2 in batch CCB0001
    sample1 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "PBMC-001", "sequencing_batch_code": "CCB0001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sample2 = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "PBMC-002", "sequencing_batch_code": "CCB0001"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await session.commit()

    s1_id = sample1.json()["id"]
    s2_id = sample2.json()["id"]

    # Get the org_id from the experiment
    org_row = (
        await session.execute(
            text("SELECT organization_id FROM experiments WHERE id = :eid"),
            {"eid": exp_id},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    # Create naming profile
    await _create_naming_profile(session, org_id)
    await session.flush()

    # Process manifest
    manifest = (
        "# batch: CCB0001\n"
        "abc123def456  pbmc_1k_v3_S1_L001_R1_001.fastq.gz\n"
        "789abcdef012  pbmc_1k_v3_S2_L001_R1_001.fastq.gz\n"
    )

    batch = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=org_id,
        source_bucket="test-bucket",
        db=session,
    )

    # Verify manifest entries resolved to correct samples
    entries = (
        await session.execute(
            text(
                "SELECT expected_filename, resolved_sample_id FROM manifest_entries "
                "WHERE sequencing_batch_id = :bid ORDER BY id"
            ).bindparams(bid=batch.id)
        )
    ).fetchall()

    assert len(entries) == 2
    # S1 -> sample at position 1 -> PBMC-001
    assert entries[0][1] == s1_id, f"S1 should resolve to sample {s1_id}, got {entries[0][1]}"
    # S2 -> sample at position 2 -> PBMC-002
    assert entries[1][1] == s2_id, f"S2 should resolve to sample {s2_id}, got {entries[1][1]}"


@pytest.mark.asyncio
async def test_manifest_unknown_position_stays_unresolved(client: AsyncClient, admin_token: str, session: AsyncSession):
    """S-number with no sample at that position stays unresolved."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Unknown Pos Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Unknown Pos Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    # Create only 1 sample at position 1
    await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "ONLY-ONE", "sequencing_batch_code": "CCB0002"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await session.commit()

    org_row = (
        await session.execute(
            text("SELECT organization_id FROM experiments WHERE id = :eid"),
            {"eid": exp_id},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    await _create_naming_profile(session, org_id)
    await session.flush()

    # Manifest references S99 which doesn't exist
    manifest = "# batch: CCB0002\nhash123  pbmc_1k_v3_S99_L001_R1_001.fastq.gz\n"

    batch = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=org_id,
        source_bucket="test-bucket",
        db=session,
    )

    entries = (
        await session.execute(
            text("SELECT resolved_sample_id, status FROM manifest_entries WHERE sequencing_batch_id = :bid").bindparams(
                bid=batch.id
            )
        )
    ).fetchall()

    assert len(entries) == 1
    assert entries[0][0] is None  # Not resolved
    assert entries[0][1] == "pending"


@pytest.mark.asyncio
async def test_manifest_paired_end_same_sample(client: AsyncClient, admin_token: str, session: AsyncSession):
    """R1 and R2 files for the same S-number both resolve to the same sample."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Paired End Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp = await client.post(
        "/api/experiments",
        json={"name": "Paired End Exp", "project_id": proj.json()["id"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    sample_resp = await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "PAIR-001", "sequencing_batch_code": "CCB0003"},
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

    await _create_naming_profile(session, org_id)
    await session.flush()

    manifest = (
        "# batch: CCB0003\nhash1  pbmc_1k_v3_S1_L001_R1_001.fastq.gz\nhash2  pbmc_1k_v3_S1_L001_R2_001.fastq.gz\n"
    )

    batch = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=org_id,
        source_bucket="test-bucket",
        db=session,
    )

    entries = (
        await session.execute(
            text(
                "SELECT resolved_sample_id FROM manifest_entries WHERE sequencing_batch_id = :bid ORDER BY id"
            ).bindparams(bid=batch.id)
        )
    ).fetchall()

    assert len(entries) == 2
    # Both R1 and R2 resolve to the same sample
    assert entries[0][0] == sample_id
    assert entries[1][0] == sample_id


@pytest.mark.asyncio
async def test_manifest_derives_experiment_from_sample(client: AsyncClient, admin_token: str, session: AsyncSession):
    """When sample resolves, experiment_id and project_id should be derived from it."""
    proj = await client.post(
        "/api/projects",
        json={"name": "Derive Exp Project", "status": "active"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    proj_id = proj.json()["id"]
    exp = await client.post(
        "/api/experiments",
        json={"name": "Derive Exp Exp", "project_id": proj_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    exp_id = exp.json()["id"]

    await client.post(
        f"/api/experiments/{exp_id}/samples",
        json={"sample_id_unique": "DERIVE-001", "sequencing_batch_code": "CCB0004"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await session.commit()

    org_row = (
        await session.execute(
            text("SELECT organization_id FROM experiments WHERE id = :eid"),
            {"eid": exp_id},
        )
    ).fetchone()
    assert org_row is not None
    org_id = org_row[0]

    await _create_naming_profile(session, org_id)
    await session.flush()

    manifest = "# batch: CCB0004\nhash1  pbmc_1k_v3_S1_L001_R1_001.fastq.gz\n"

    batch = await process_manifest_ingest(
        manifest_content=manifest,
        manifest_format="md5sum",
        org_id=org_id,
        source_bucket="test-bucket",
        db=session,
    )

    entries = (
        await session.execute(
            text(
                "SELECT resolved_sample_id, resolved_experiment_id, resolved_project_id "
                "FROM manifest_entries WHERE sequencing_batch_id = :bid"
            ).bindparams(bid=batch.id)
        )
    ).fetchall()

    assert len(entries) == 1
    assert entries[0][0] is not None, "sample should be resolved"
    assert entries[0][1] == exp_id, f"experiment should be {exp_id}, got {entries[0][1]}"
    assert entries[0][2] == proj_id, f"project should be {proj_id}, got {entries[0][2]}"
