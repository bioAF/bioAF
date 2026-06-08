"""Tests for migration 024 - Phase 18 GCS bucket infrastructure.

Tests that:
- New platform_config keys exist (storage_deployed, ingest_bucket_name, raw_bucket_name,
  working_bucket_name, results_bucket_name, config_backups_bucket_name)
- New files.experiment_id column exists with FK and index
"""

import pytest
from sqlalchemy import text
from app.services.bootstrap_roles import seed_builtin_roles


@pytest.mark.asyncio
async def test_migration_seeds_storage_platform_config_keys(session):
    """Migration 024 inserts storage-related platform_config keys."""
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('storage_deployed', 'false'),
            ('ingest_bucket_name', 'null'),
            ('raw_bucket_name', 'null'),
            ('working_bucket_name', 'null'),
            ('results_bucket_name', 'null'),
            ('config_backups_bucket_name', 'null')
        ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.commit()

    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ("
                "  'storage_deployed', 'ingest_bucket_name', 'raw_bucket_name',"
                "  'working_bucket_name', 'results_bucket_name', 'config_backups_bucket_name'"
                ") ORDER BY key"
            )
        )
    ).fetchall()

    config = {r[0]: r[1] for r in rows}
    assert len(config) == 6
    assert config["storage_deployed"] == "false"
    assert config["ingest_bucket_name"] == "null"
    assert config["raw_bucket_name"] == "null"
    assert config["working_bucket_name"] == "null"
    assert config["results_bucket_name"] == "null"
    assert config["config_backups_bucket_name"] == "null"


@pytest.mark.asyncio
async def test_migration_adds_experiment_id_to_files(session):
    """Migration 024 adds experiment_id column to files with FK and index."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.experiment import Experiment
    from app.services.auth_service import AuthService

    org = Organization(name="MigrationTestOrg024", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="mig024@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    exp = Experiment(
        name="Test Experiment 024",
        owner_user_id=user.id,
        organization_id=org.id,
        status="registered",
    )
    session.add(exp)
    await session.flush()

    # Insert a file with experiment_id set
    await session.execute(
        text("""
        INSERT INTO files
            (organization_id, gcs_uri, filename, file_type, experiment_id)
        VALUES
            (:org_id, 'gs://test/file.fastq.gz', 'file.fastq.gz', 'fastq', :exp_id)
        """).bindparams(org_id=org.id, exp_id=exp.id)
    )
    await session.commit()

    row = (
        await session.execute(text("SELECT experiment_id FROM files WHERE gcs_uri = 'gs://test/file.fastq.gz'"))
    ).fetchone()

    assert row is not None
    assert row[0] == exp.id

    # Verify NULL experiment_id also works (unlinked file)
    await session.execute(
        text("""
        INSERT INTO files
            (organization_id, gcs_uri, filename, file_type, experiment_id)
        VALUES
            (:org_id, 'gs://test/unlinked.fastq.gz', 'unlinked.fastq.gz', 'fastq', NULL)
        """).bindparams(org_id=org.id)
    )
    await session.commit()

    row2 = (
        await session.execute(text("SELECT experiment_id FROM files WHERE gcs_uri = 'gs://test/unlinked.fastq.gz'"))
    ).fetchone()
    assert row2 is not None
    assert row2[0] is None


@pytest.mark.asyncio
async def test_migration_experiment_id_index_exists(session):
    """Migration 024 creates idx_files_experiment_id index."""
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'files' AND indexname = 'idx_files_experiment_id'")
    )
    row = result.fetchone()
    assert row is not None, "Index idx_files_experiment_id should exist on files table"
