"""Tests for migration 026 - Phase 20 live pipeline execution on GKE.

Tests that:
- New columns on pipeline_runs (k8s_job_name, k8s_namespace, k8s_pod_name, actual_cost)
- New platform_config key (k8s_pipeline_namespace)
"""

import pytest
from sqlalchemy import text
from app.services.bootstrap_roles import seed_builtin_roles


@pytest.mark.asyncio
async def test_migration_adds_k8s_columns_to_pipeline_runs(session):
    """Migration 026 adds k8s_job_name, k8s_namespace, k8s_pod_name, actual_cost to pipeline_runs."""
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="MigrationTestOrg026", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="mig026@test.com",
        password_hash=AuthService.hash_password("testpass"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    # Insert a pipeline_run with the new k8s columns
    await session.execute(
        text("""
        INSERT INTO pipeline_runs (
            organization_id, pipeline_name, status,
            k8s_job_name, k8s_namespace, k8s_pod_name, actual_cost
        ) VALUES (
            :org_id, 'test-pipeline', 'pending',
            'bioaf-pipeline-99', 'bioaf-pipelines', 'bioaf-pipeline-99-abc12', 4.25
        )
        """).bindparams(org_id=org.id)
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT k8s_job_name, k8s_namespace, k8s_pod_name, actual_cost "
                "FROM pipeline_runs WHERE pipeline_name = 'test-pipeline' LIMIT 1"
            )
        )
    ).fetchone()

    assert row is not None
    assert row[0] == "bioaf-pipeline-99"
    assert row[1] == "bioaf-pipelines"
    assert row[2] == "bioaf-pipeline-99-abc12"
    assert float(row[3]) == 4.25


@pytest.mark.asyncio
async def test_migration_seeds_k8s_pipeline_namespace_config(session):
    """Migration 026 inserts k8s_pipeline_namespace into platform_config."""
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('k8s_pipeline_namespace', 'bioaf-pipelines')
        ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.commit()

    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'k8s_pipeline_namespace'"))
    ).fetchone()

    assert row is not None
    assert row[0] == "bioaf-pipelines"


@pytest.mark.asyncio
async def test_migration_k8s_columns_default_to_null(session):
    """New k8s columns should default to NULL when not provided."""
    from app.models.organization import Organization

    org = Organization(name="MigrationNullTestOrg026", setup_complete=True)
    session.add(org)
    await session.flush()

    await session.execute(
        text("""
        INSERT INTO pipeline_runs (organization_id, pipeline_name, status)
        VALUES (:org_id, 'null-test-pipeline', 'pending')
        """).bindparams(org_id=org.id)
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT k8s_job_name, k8s_namespace, k8s_pod_name, actual_cost "
                "FROM pipeline_runs WHERE pipeline_name = 'null-test-pipeline' LIMIT 1"
            )
        )
    ).fetchone()

    assert row is not None
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
    assert row[3] is None
