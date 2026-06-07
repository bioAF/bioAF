"""Tests for migration 101 - neutral compute_job_ref + provider_metadata columns.

BAL rework, Phase 4: pipeline_run and notebook_session carry Kubernetes-named
columns (k8s_job_name / k8s_namespace / k8s_pod_name) that a non-K8s backend
(SLURM) cannot honestly fill. This additive migration introduces backend-neutral
columns and backfills them from the existing k8s_* values. The old columns are
left in place (a later migration drops them once all callers are migrated), so
this step is fully reversible.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_FILE = MIGRATIONS_DIR / "101_neutral_compute_job_ref.py"


def test_migration_file_exists():
    assert MIGRATION_FILE.exists(), f"Expected migration at {MIGRATION_FILE}"


def test_migration_chains_to_100():
    content = MIGRATION_FILE.read_text()
    assert 'revision = "101"' in content
    assert 'down_revision = "100"' in content


def test_migration_adds_and_drops_neutral_columns():
    content = MIGRATION_FILE.read_text()
    # additive on both tables (notebook sessions live in the compute_sessions table)
    assert "pipeline_runs" in content
    assert "compute_sessions" in content
    assert "add_column" in content
    assert "compute_job_ref" in content
    assert "provider_metadata" in content
    # reversible: downgrade drops them
    assert "drop_column" in content


@pytest.mark.asyncio
async def test_backfill_sql_copies_k8s_values(session, admin_user):
    """The backfill copies the job handle into compute_job_ref and the K8s
    specifics into provider_metadata, for rows that have any k8s_* value."""
    await session.execute(
        text(
            "INSERT INTO pipeline_runs (id, organization_id, pipeline_name, status, "
            "k8s_job_name, k8s_namespace, k8s_pod_name) VALUES "
            "(9101, :org, 'nf-core/scrnaseq', 'completed', 'job-abc', 'bioaf-pipelines', 'pod-xyz')"
        ),
        {"org": admin_user.organization_id},
    )
    await session.commit()

    # The exact backfill the migration runs for pipeline_runs.
    await session.execute(
        text(
            "UPDATE pipeline_runs SET "
            "compute_job_ref = k8s_job_name, "
            "provider_metadata = jsonb_strip_nulls(jsonb_build_object("
            "'job_name', k8s_job_name, 'namespace', k8s_namespace, 'pod_name', k8s_pod_name)) "
            "WHERE k8s_job_name IS NOT NULL OR k8s_namespace IS NOT NULL OR k8s_pod_name IS NOT NULL"
        )
    )
    await session.commit()

    row = (
        await session.execute(
            text("SELECT compute_job_ref, provider_metadata FROM pipeline_runs WHERE id = 9101")
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "job-abc"
    assert row[1] == {"job_name": "job-abc", "namespace": "bioaf-pipelines", "pod_name": "pod-xyz"}
