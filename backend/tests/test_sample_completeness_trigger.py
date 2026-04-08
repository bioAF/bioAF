"""Tests for Chunk 7: Sample completeness pipeline trigger."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manifest_entry import ManifestEntry
from app.models.sequencing_batch import SequencingBatch
from app.services.sample_completeness_service import check_sample_completeness

pytestmark = pytest.mark.asyncio


async def _setup_scenario(session: AsyncSession) -> dict:
    """Create org, experiment, sample, sequencing batch, and manifest entries."""
    from app.models.organization import Organization
    from app.models.experiment import Experiment
    from app.models.sample import Sample
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="Trigger Test Org", setup_complete=True)
    session.add(org)
    await session.flush()
    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="trigger@test.com",
        password_hash=AuthService.hash_password("pass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(
        organization_id=org.id,
        name="Trigger Exp",
        status="registered",
        owner_user_id=user.id,
    )
    session.add(exp)
    await session.flush()

    batch = SequencingBatch(
        organization_id=org.id,
        name="Trigger Batch",
        code="SEQ-TRIG-001",
        status="ingesting",
        expected_file_count=4,
    )
    session.add(batch)
    await session.flush()

    s1 = Sample(
        experiment_id=exp.id,
        sample_id_unique="SAMPLE_A",
        sequencing_batch_id=batch.id,
        status="registered",
    )
    s2 = Sample(
        experiment_id=exp.id,
        sample_id_unique="SAMPLE_B",
        sequencing_batch_id=batch.id,
        status="registered",
    )
    session.add_all([s1, s2])
    await session.flush()

    # Manifest entries: 2 files per sample (R1 + R2)
    e1_r1 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="SAMPLE_A_R1.fastq.gz",
        expected_md5="hash1",
        resolved_sample_id=s1.id,
        resolved_experiment_id=exp.id,
        status="pending",
    )
    e1_r2 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="SAMPLE_A_R2.fastq.gz",
        expected_md5="hash2",
        resolved_sample_id=s1.id,
        resolved_experiment_id=exp.id,
        status="pending",
    )
    e2_r1 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="SAMPLE_B_R1.fastq.gz",
        expected_md5="hash3",
        resolved_sample_id=s2.id,
        resolved_experiment_id=exp.id,
        status="pending",
    )
    e2_r2 = ManifestEntry(
        sequencing_batch_id=batch.id,
        expected_filename="SAMPLE_B_R2.fastq.gz",
        expected_md5="hash4",
        resolved_sample_id=s2.id,
        resolved_experiment_id=exp.id,
        status="pending",
    )
    session.add_all([e1_r1, e1_r2, e2_r1, e2_r2])
    await session.flush()
    await session.commit()

    return {
        "org_id": org.id,
        "experiment_id": exp.id,
        "batch_id": batch.id,
        "sample_a_id": s1.id,
        "sample_b_id": s2.id,
        "entries": {
            "a_r1": e1_r1.id,
            "a_r2": e1_r2.id,
            "b_r1": e2_r1.id,
            "b_r2": e2_r2.id,
        },
    }


async def test_incomplete_sample_returns_false(session):
    """When only R1 is verified, sample is not complete."""
    ctx = await _setup_scenario(session)

    # Verify only R1
    await session.execute(
        ManifestEntry.__table__.update().where(ManifestEntry.id == ctx["entries"]["a_r1"]).values(status="verified")
    )
    await session.commit()

    result = await check_sample_completeness(session, ctx["sample_a_id"], ctx["batch_id"])
    assert result is False


async def test_complete_sample_returns_true(session):
    """When both R1 and R2 are verified, sample is complete."""
    ctx = await _setup_scenario(session)

    # Verify both R1 and R2
    for eid in [ctx["entries"]["a_r1"], ctx["entries"]["a_r2"]]:
        await session.execute(ManifestEntry.__table__.update().where(ManifestEntry.id == eid).values(status="verified"))
    await session.commit()

    result = await check_sample_completeness(session, ctx["sample_a_id"], ctx["batch_id"])
    assert result is True


async def test_completing_one_sample_does_not_affect_other(session):
    """Completing sample A should not make sample B appear complete."""
    ctx = await _setup_scenario(session)

    # Verify both files for sample A
    for eid in [ctx["entries"]["a_r1"], ctx["entries"]["a_r2"]]:
        await session.execute(ManifestEntry.__table__.update().where(ManifestEntry.id == eid).values(status="verified"))
    await session.commit()

    # Sample A is complete
    assert await check_sample_completeness(session, ctx["sample_a_id"], ctx["batch_id"]) is True
    # Sample B is not
    assert await check_sample_completeness(session, ctx["sample_b_id"], ctx["batch_id"]) is False
