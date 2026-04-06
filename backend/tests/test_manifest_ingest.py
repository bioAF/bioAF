"""Tests for Chunk 4: Manifest-driven ingest flow."""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.manifest_ingest_service import process_manifest_ingest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def org_and_experiment(session: AsyncSession):
    """Create org, user, experiment, naming profile, and samples for manifest tests."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.experiment import Experiment
    from app.models.sample import Sample
    from app.models.naming_profile import NamingProfile
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Manifest Test Org", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="ingest@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(
        organization_id=org.id,
        name="EXP015",
        status="registered",
        owner_user_id=user.id,
    )
    session.add(exp)
    await session.flush()

    # Create naming profile that maps filenames to samples
    profile = NamingProfile(
        organization_id=org.id,
        name="CRO Standard",
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

    # Create samples
    s1 = Sample(
        experiment_id=exp.id,
        sample_id_external="SAMPLE0003",
        status="registered",
    )
    s2 = Sample(
        experiment_id=exp.id,
        sample_id_external="SAMPLE0007",
        status="registered",
    )
    session.add_all([s1, s2])
    await session.flush()

    # Add platform config for manifest settings
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
        "sample_ids": [s1.id, s2.id],
    }


MANIFEST_CONTENT = (
    "# batch: SEQ-2026-0042\n"
    "d41d8cd98f00b204e9800998ecf8427e  EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz\n"
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6  EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz\n"
    "e5f6a7b8c9d0e1f2a3b4c5d6d41d8cd9  EXP015_SAMPLE0007_S7_L001_R1_001.fastq.gz\n"
    "f6a7b8c9d0e1f2a3b4c5d6d41d8cd98f  EXP015_SAMPLE0007_S7_L001_R2_001.fastq.gz\n"
)


async def test_manifest_creates_sequencing_batch(session, org_and_experiment):
    """Parsing a manifest should create a SequencingBatch record."""
    ctx = org_and_experiment

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    result = await session.execute(select(SequencingBatch).where(SequencingBatch.code == "SEQ-2026-0042"))
    batch = result.scalar_one_or_none()
    assert batch is not None
    assert batch.status == "ingesting"
    assert batch.expected_file_count == 4


async def test_manifest_creates_manifest_entries(session, org_and_experiment):
    """Each manifest line should create a ManifestEntry."""
    ctx = org_and_experiment

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    result = await session.execute(select(ManifestEntry))
    entries = result.scalars().all()
    assert len(entries) == 4

    filenames = {e.expected_filename for e in entries}
    assert "EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz" in filenames
    assert "EXP015_SAMPLE0007_S7_L001_R2_001.fastq.gz" in filenames


async def test_manifest_resolves_samples(session, org_and_experiment):
    """Manifest entries should resolve to the correct samples via naming profiles."""
    ctx = org_and_experiment

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    result = await session.execute(
        select(ManifestEntry).where(ManifestEntry.expected_filename == "EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz")
    )
    entry = result.scalar_one()
    assert entry.resolved_sample_id is not None
    assert entry.resolved_experiment_id is not None


async def test_manifest_entries_start_pending(session, org_and_experiment):
    """All entries should start with status 'pending' since files aren't checked yet."""
    ctx = org_and_experiment

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    result = await session.execute(select(ManifestEntry))
    entries = result.scalars().all()
    assert all(e.status == "pending" for e in entries)


async def test_batch_status_ingesting_when_all_pending(session, org_and_experiment):
    """POBatch status should be 'ingesting' when entries are pending."""
    ctx = org_and_experiment

    await process_manifest_ingest(
        manifest_content=MANIFEST_CONTENT,
        manifest_format="md5sum",
        org_id=ctx["org_id"],
        source_bucket="ingest-bucket",
        db=session,
    )
    await session.commit()

    result = await session.execute(select(SequencingBatch).where(SequencingBatch.code == "SEQ-2026-0042"))
    batch = result.scalar_one()
    assert batch.status == "ingesting"
