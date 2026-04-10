"""Tests for manifest reconciliation fixes.

Covers:
1. Retroactive reconciliation (files arrive before manifest)
2. Forward-path query scoping (MD5 tightening)
3. Content-aware redelivery guard
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experiment import Experiment
from app.models.file import File
from app.models.manifest_entry import ManifestEntry
from app.models.naming_profile import NamingProfile
from app.models.sample import Sample, sample_files
from app.models.sequencing_batch import SequencingBatch
from app.services.manifest_ingest_service import process_manifest_ingest

pytestmark = pytest.mark.asyncio


# ---- Manifest content ----

MANIFEST_CONTENT = (
    "# batch: RECON-001\n"
    "aaa111aaa111aaa111aaa111aaa111aa  EXP_SAMP01_S1_L001_R1_001.fastq.gz\n"
    "bbb222bbb222bbb222bbb222bbb222bb  EXP_SAMP01_S1_L001_R2_001.fastq.gz\n"
)

MANIFEST_CONTENT_UPDATED = (
    "# batch: RECON-001\n"
    "aaa111aaa111aaa111aaa111aaa111aa  EXP_SAMP01_S1_L001_R1_001.fastq.gz\n"
    "bbb222bbb222bbb222bbb222bbb222bb  EXP_SAMP01_S1_L001_R2_001.fastq.gz\n"
    "ccc333ccc333ccc333ccc333ccc333cc  EXP_SAMP01_S1_L001_R3_001.fastq.gz\n"
)


# ---- Fixtures ----


@pytest_asyncio.fixture
async def recon_ctx(session: AsyncSession):
    """Create org, user, experiment, naming profile, and sample for reconciliation tests."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Recon Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="recon@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(
        organization_id=org.id,
        name="EXP",
        status="registered",
        owner_user_id=user.id,
    )
    session.add(exp)
    await session.flush()

    # Naming profile: EXP_SAMP01_S1_L001_R1_001.fastq.gz
    profile = NamingProfile(
        organization_id=org.id,
        name="Recon Profile",
        delimiter="_",
        strip_extension=True,
        segments_json=[
            {"position": 0, "field": "experiment_code", "required": True},
            {"position": 1, "field": "sample_id", "required": True},
            {"position": 2, "field": "ignore", "required": True},
            {"position": 3, "field": "ignore", "required": True},
            {"position": 4, "field": "ignore", "required": True},
            {"position": 5, "field": "ignore", "required": True},
        ],
        status="active",
        created_by=user.id,
    )
    session.add(profile)
    await session.flush()

    sample = Sample(
        experiment_id=exp.id,
        sample_id_unique="SAMP01",
        status="registered",
    )
    session.add(sample)
    await session.flush()

    for key, val in [
        ("manifest_filename", "md5.txt"),
        ("manifest_format", "md5sum"),
        ("manifest_retry_interval_minutes", "15"),
        ("manifest_max_retries", "48"),
        ("raw_bucket_name", "test-raw-bucket"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=val)
        )

    await session.commit()

    return {
        "org_id": org.id,
        "user_id": user.id,
        "experiment_id": exp.id,
        "sample_id": sample.id,
    }


def _make_file(org_id: int, filename: str, md5: str) -> File:
    """Create an in-memory File record (caller must session.add + flush)."""
    return File(
        organization_id=org_id,
        gcs_uri=f"gs://ingest-bucket/{filename}",
        filename=filename,
        size_bytes=1024,
        md5_checksum=md5,
        file_type="fastq",
    )


# ---- Fix 1: Retroactive reconciliation ----


async def test_retroactive_reconciliation_links_files(session, recon_ctx):
    """Files ingested before manifest should be linked when manifest arrives."""
    ctx = recon_ctx

    # Step 1: Files arrive first (no manifest entries exist yet)
    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    f2 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R2_001.fastq.gz", "bbb222bbb222bbb222bbb222bbb222bb")
    session.add_all([f1, f2])
    await session.flush()
    await session.commit()

    # Step 2: Manifest arrives
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # Entries should be verified with file_id linked
    entries = (await session.execute(select(ManifestEntry))).scalars().all()
    assert len(entries) == 2
    assert all(e.status == "verified" for e in entries)
    assert all(e.file_id is not None for e in entries)
    assert {e.file_id for e in entries} == {f1.id, f2.id}


async def test_retroactive_reconciliation_links_sample_files(session, recon_ctx):
    """Retroactive reconciliation should create sample_files junction rows."""
    ctx = recon_ctx

    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    session.add(f1)
    await session.flush()
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    links = (
        await session.execute(
            sample_files.select().where(
                sample_files.c.sample_id == ctx["sample_id"],
                sample_files.c.file_id == f1.id,
            )
        )
    ).fetchall()
    assert len(links) == 1


async def test_retroactive_reconciliation_updates_batch_count(session, recon_ctx):
    """Retroactive reconciliation should increment batch ingested_file_count."""
    ctx = recon_ctx

    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    f2 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R2_001.fastq.gz", "bbb222bbb222bbb222bbb222bbb222bb")
    session.add_all([f1, f2])
    await session.flush()
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    batch = (await session.execute(select(SequencingBatch).where(SequencingBatch.code == "RECON-001"))).scalar_one()
    assert batch.ingested_file_count == 2


async def test_retroactive_reconciliation_sets_file_experiment(session, recon_ctx):
    """Retroactive reconciliation should set file.experiment_id and file.sequencing_batch_id."""
    ctx = recon_ctx

    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    session.add(f1)
    await session.flush()
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    await session.refresh(f1)
    assert f1.experiment_id == ctx["experiment_id"]
    assert f1.sequencing_batch_id is not None


async def test_retroactive_recon_skips_md5_mismatch(session, recon_ctx):
    """Files with wrong MD5 should not be retroactively matched."""
    ctx = recon_ctx

    # File has wrong MD5
    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "wrong_md5_wrong_md5_wrong_md5_ww")
    session.add(f1)
    await session.flush()
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    entry = (
        await session.execute(
            select(ManifestEntry).where(ManifestEntry.expected_filename == "EXP_SAMP01_S1_L001_R1_001.fastq.gz")
        )
    ).scalar_one()
    assert entry.status == "pending"
    assert entry.file_id is None


async def test_retroactive_recon_skips_old_files(session, recon_ctx):
    """Files created more than 2 hours before the manifest should not match."""
    ctx = recon_ctx

    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    session.add(f1)
    await session.flush()

    # Backdate the file to 3 hours ago
    await session.execute(
        text("UPDATE files SET created_at = :ts WHERE id = :fid").bindparams(
            ts=datetime.now(timezone.utc) - timedelta(hours=3),
            fid=f1.id,
        )
    )
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    entry = (
        await session.execute(
            select(ManifestEntry).where(ManifestEntry.expected_filename == "EXP_SAMP01_S1_L001_R1_001.fastq.gz")
        )
    ).scalar_one()
    assert entry.status == "pending"
    assert entry.file_id is None


async def test_retroactive_recon_skips_already_linked_files(session, recon_ctx):
    """Files already linked to another ManifestEntry should not be reused."""
    ctx = recon_ctx

    # Create a file already linked to a different batch's entry
    old_batch = SequencingBatch(
        organization_id=ctx["org_id"],
        code="OLD-BATCH",
        status="complete",
        expected_file_count=1,
    )
    session.add(old_batch)
    await session.flush()

    f1 = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    session.add(f1)
    await session.flush()

    old_entry = ManifestEntry(
        sequencing_batch_id=old_batch.id,
        expected_filename="EXP_SAMP01_S1_L001_R1_001.fastq.gz",
        expected_md5="aaa111aaa111aaa111aaa111aaa111aa",
        status="verified",
        file_id=f1.id,
    )
    session.add(old_entry)
    await session.flush()
    await session.commit()

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # The new entry should NOT be linked to the already-claimed file
    new_entry = (
        await session.execute(
            select(ManifestEntry).where(
                ManifestEntry.expected_filename == "EXP_SAMP01_S1_L001_R1_001.fastq.gz",
                ManifestEntry.sequencing_batch_id != old_batch.id,
            )
        )
    ).scalar_one()
    assert new_entry.status == "pending"
    assert new_entry.file_id is None


# ---- Fix 2: Forward-path query scoping ----


async def test_forward_path_scoped_by_md5(session, recon_ctx):
    """Forward-path reconciliation should match by MD5, not just filename."""
    from app.services.ingest_service import process_ingest_event

    ctx = recon_ctx

    # Create two batches with same filename but different MD5
    batch_a = SequencingBatch(organization_id=ctx["org_id"], code="BATCH-A", status="ingesting", expected_file_count=1)
    batch_b = SequencingBatch(organization_id=ctx["org_id"], code="BATCH-B", status="ingesting", expected_file_count=1)
    session.add_all([batch_a, batch_b])
    await session.flush()

    entry_a = ManifestEntry(
        sequencing_batch_id=batch_a.id,
        expected_filename="shared_name.fastq.gz",
        expected_md5="aaaa_batch_a_md5_aaaa_batch_a_md",
        resolved_sample_id=ctx["sample_id"],
        resolved_experiment_id=ctx["experiment_id"],
        status="pending",
    )
    entry_b = ManifestEntry(
        sequencing_batch_id=batch_b.id,
        expected_filename="shared_name.fastq.gz",
        expected_md5="bbbb_batch_b_md5_bbbb_batch_b_md",
        resolved_sample_id=ctx["sample_id"],
        resolved_experiment_id=ctx["experiment_id"],
        status="pending",
    )
    session.add_all([entry_a, entry_b])
    await session.flush()
    await session.commit()

    # Ingest a file matching batch B's MD5
    await process_ingest_event(
        filename="shared_name.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="incoming/shared_name.fastq.gz",
        org_id=ctx["org_id"],
        db=session,
        content_md5="bbbb_batch_b_md5_bbbb_batch_b_md",
    )
    await session.commit()

    await session.refresh(entry_a)
    await session.refresh(entry_b)

    # entry_b should be verified (MD5 match), entry_a should still be pending
    assert entry_b.status == "verified"
    assert entry_b.file_id is not None
    assert entry_a.status == "pending"
    assert entry_a.file_id is None


# ---- Fix 3: Content-aware redelivery guard ----


async def test_redelivery_guard_true_redelivery(session, recon_ctx):
    """Reprocessing the same manifest should not create duplicate entries."""
    ctx = recon_ctx

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # Process the same manifest again
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    entries = (await session.execute(select(ManifestEntry))).scalars().all()
    assert len(entries) == 2  # Still 2, not 4


async def test_redelivery_guard_updated_manifest(session, recon_ctx):
    """An updated manifest with new entries should add only the new ones."""
    ctx = recon_ctx

    # First manifest: 2 entries
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # Updated manifest: 3 entries (2 existing + 1 new)
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT_UPDATED,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    entries = (await session.execute(select(ManifestEntry))).scalars().all()
    assert len(entries) == 3

    filenames = {e.expected_filename for e in entries}
    assert "EXP_SAMP01_S1_L001_R3_001.fastq.gz" in filenames


async def test_redelivery_guard_preserves_verified_entries(session, recon_ctx):
    """Reprocessing should never delete entries that are already verified."""
    ctx = recon_ctx

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # Manually verify one entry (simulating a file that was reconciled)
    real_file = _make_file(ctx["org_id"], "EXP_SAMP01_S1_L001_R1_001.fastq.gz", "aaa111aaa111aaa111aaa111aaa111aa")
    session.add(real_file)
    await session.flush()

    entry = (
        await session.execute(
            select(ManifestEntry).where(ManifestEntry.expected_filename == "EXP_SAMP01_S1_L001_R1_001.fastq.gz")
        )
    ).scalar_one()
    entry.status = "verified"
    entry.file_id = real_file.id
    await session.flush()
    await session.commit()

    # Reprocess with updated manifest (which has a different entry set)
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT_UPDATED,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # The verified entry should still exist with its file_id
    entry = (
        await session.execute(
            select(ManifestEntry).where(ManifestEntry.expected_filename == "EXP_SAMP01_S1_L001_R1_001.fastq.gz")
        )
    ).scalar_one()
    assert entry.status == "verified"
    assert entry.file_id == real_file.id


# ---- Forward path still works ----


async def test_forward_path_manifest_then_files(session, recon_ctx):
    """The existing forward path (manifest first, files second) should still work."""
    from app.services.ingest_service import process_ingest_event

    ctx = recon_ctx

    # Step 1: Manifest arrives first
    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    # Step 2: Files arrive
    await process_ingest_event(
        filename="EXP_SAMP01_S1_L001_R1_001.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="incoming/EXP_SAMP01_S1_L001_R1_001.fastq.gz",
        org_id=ctx["org_id"],
        db=session,
        content_md5="aaa111aaa111aaa111aaa111aaa111aa",
    )
    await process_ingest_event(
        filename="EXP_SAMP01_S1_L001_R2_001.fastq.gz",
        source_bucket="ingest-bucket",
        source_path="incoming/EXP_SAMP01_S1_L001_R2_001.fastq.gz",
        org_id=ctx["org_id"],
        db=session,
        content_md5="bbb222bbb222bbb222bbb222bbb222bb",
    )
    await session.commit()

    entries = (await session.execute(select(ManifestEntry))).scalars().all()
    assert len(entries) == 2
    assert all(e.status == "verified" for e in entries)
    assert all(e.file_id is not None for e in entries)

    # Files should be linked to sample
    links = (
        await session.execute(sample_files.select().where(sample_files.c.sample_id == ctx["sample_id"]))
    ).fetchall()
    assert len(links) == 2
