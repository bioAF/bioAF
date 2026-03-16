"""Tests for File Organization Service.

Tests:
10. assign_file_to_experiment moves from unlinked
11. assign writes audit log
12. reassign between experiments
13. unlink moves to unlinked
14. assign when already assigned is treated as reassign
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text


async def _seed_org_user_exp(session):
    """Helper to create org, user, experiment, and seed platform_config."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.experiment import Experiment
    from app.services.auth_service import AuthService

    org = Organization(name="FileOrgTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    user = User(
        email="fileorg@test.com",
        password_hash=AuthService.hash_password("pw"),
        role="admin",
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp1 = Experiment(name="Exp One", owner_user_id=user.id, organization_id=org.id, status="registered")
    exp2 = Experiment(name="Exp Two", owner_user_id=user.id, organization_id=org.id, status="registered")
    session.add_all([exp1, exp2])
    await session.flush()

    # Seed bucket names
    for key, value in [
        ("storage_deployed", "true"),
        ("raw_bucket_name", "bioaf-raw-demo"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()

    return org, user, exp1, exp2


async def _create_file(session, org_id, gcs_uri, experiment_id=None):
    """Insert a file record and return its id."""
    result = await session.execute(
        text("""
        INSERT INTO files (organization_id, gcs_uri, filename, file_type, experiment_id)
        VALUES (:org_id, :uri, :fname, 'fastq', :exp_id)
        RETURNING id
        """).bindparams(
            org_id=org_id,
            uri=gcs_uri,
            fname=gcs_uri.split("/")[-1],
            exp_id=experiment_id,
        )
    )
    await session.commit()
    return result.scalar_one()


@pytest.mark.asyncio
async def test_assign_file_to_experiment_moves_from_unlinked(session):
    """Create file in unlinked prefix. Assign. Assert moved and DB updated."""
    org, user, exp1, _ = await _seed_org_user_exp(session)

    file_id = await _create_file(session, org.id, "gs://bioaf-raw-demo/unlinked/sample.fastq.gz")

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value="gs://bioaf-raw-demo/experiments/1/sample.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_experiment_prefix.return_value = f"experiments/{exp1.id}/"
        mock_gcs.build_unlinked_prefix.return_value = "unlinked/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.assign_file_to_experiment(session, file_id, exp1.id, user.id)

    row = (
        await session.execute(text("SELECT experiment_id, gcs_uri FROM files WHERE id = :fid").bindparams(fid=file_id))
    ).fetchone()
    assert row[0] == exp1.id
    assert "experiments/" in row[1]


@pytest.mark.asyncio
async def test_assign_file_from_ingest_bucket_moves_to_raw_bucket(session):
    """File in ingest bucket should move to raw bucket, not stay in ingest."""
    org, user, exp1, _ = await _seed_org_user_exp(session)

    # File lives in the ingest bucket (manual upload path)
    file_id = await _create_file(session, org.id, "gs://bioaf-ingest-demo/uploads/abc123/reads.fastq.gz")

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value=f"gs://bioaf-raw-demo/experiments/{exp1.id}/reads.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_experiment_prefix.return_value = f"experiments/{exp1.id}/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.assign_file_to_experiment(session, file_id, exp1.id, user.id)

        # The move_file call should target the raw bucket, not the ingest bucket
        call_args = mock_gcs.move_file.call_args
        destination_uri = call_args[0][1] if call_args[0] else call_args[1].get("destination_uri", "")
        assert "bioaf-raw-demo" in destination_uri, (
            f"Expected destination in raw bucket (bioaf-raw-demo), got {destination_uri}"
        )
        assert "bioaf-ingest-demo" not in destination_uri, (
            f"File should not stay in ingest bucket, got {destination_uri}"
        )


@pytest.mark.asyncio
async def test_assign_file_to_experiment_writes_audit_log(session):
    """Assign a file. Assert audit log entry created."""
    org, user, exp1, _ = await _seed_org_user_exp(session)

    file_id = await _create_file(session, org.id, "gs://bioaf-raw-demo/unlinked/audit.fastq.gz")

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value="gs://bioaf-raw-demo/experiments/1/audit.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_experiment_prefix.return_value = f"experiments/{exp1.id}/"
        mock_gcs.build_unlinked_prefix.return_value = "unlinked/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.assign_file_to_experiment(session, file_id, exp1.id, user.id)

    audit_row = (
        await session.execute(
            text(
                "SELECT action, entity_type FROM audit_log WHERE entity_type = 'file' AND entity_id = :fid"
            ).bindparams(fid=file_id)
        )
    ).fetchone()
    assert audit_row is not None
    assert audit_row[0] == "assigned_to_experiment"


@pytest.mark.asyncio
async def test_reassign_file_between_experiments(session):
    """File in experiment 1. Reassign to experiment 2. Assert moved and updated."""
    org, user, exp1, exp2 = await _seed_org_user_exp(session)

    file_id = await _create_file(
        session,
        org.id,
        f"gs://bioaf-raw-demo/experiments/{exp1.id}/data.fastq.gz",
        experiment_id=exp1.id,
    )

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value=f"gs://bioaf-raw-demo/experiments/{exp2.id}/data.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_experiment_prefix.return_value = f"experiments/{exp2.id}/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.reassign_file_to_experiment(session, file_id, exp2.id, user.id)

    row = (
        await session.execute(text("SELECT experiment_id, gcs_uri FROM files WHERE id = :fid").bindparams(fid=file_id))
    ).fetchone()
    assert row[0] == exp2.id
    assert f"experiments/{exp2.id}/" in row[1]


@pytest.mark.asyncio
async def test_unlink_file_moves_to_unlinked(session):
    """File in experiment. Unlink. Assert moved to unlinked prefix."""
    org, user, exp1, _ = await _seed_org_user_exp(session)

    file_id = await _create_file(
        session,
        org.id,
        f"gs://bioaf-raw-demo/experiments/{exp1.id}/unlink.fastq.gz",
        experiment_id=exp1.id,
    )

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value="gs://bioaf-raw-demo/unlinked/unlink.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_unlinked_prefix.return_value = "unlinked/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.unlink_file_from_experiment(session, file_id, user.id)

    row = (
        await session.execute(text("SELECT experiment_id, gcs_uri FROM files WHERE id = :fid").bindparams(fid=file_id))
    ).fetchone()
    assert row[0] is None
    assert "unlinked/" in row[1]


@pytest.mark.asyncio
async def test_assign_file_already_in_experiment_is_reassign(session):
    """File already assigned to exp1. Assign to exp2. Treated as reassignment."""
    org, user, exp1, exp2 = await _seed_org_user_exp(session)

    file_id = await _create_file(
        session,
        org.id,
        f"gs://bioaf-raw-demo/experiments/{exp1.id}/reassign.fastq.gz",
        experiment_id=exp1.id,
    )

    with patch("app.services.file_organization.GcsStorageService") as mock_gcs:
        mock_gcs.move_file = AsyncMock(return_value=f"gs://bioaf-raw-demo/experiments/{exp2.id}/reassign.fastq.gz")
        mock_gcs.get_credentials = AsyncMock(return_value=None)
        mock_gcs.build_experiment_prefix.return_value = f"experiments/{exp2.id}/"

        from app.services.file_organization import FileOrganizationService

        await FileOrganizationService.assign_file_to_experiment(session, file_id, exp2.id, user.id)

    row = (
        await session.execute(text("SELECT experiment_id FROM files WHERE id = :fid").bindparams(fid=file_id))
    ).fetchone()
    assert row[0] == exp2.id
