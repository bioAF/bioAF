"""Tests for Phase 22 migration: notebook session K8s columns and platform_config keys."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_notebook_sessions_has_k8s_columns(session):
    """New K8s columns exist on notebook_sessions table."""
    result = await session.execute(
        text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'compute_sessions'
            AND column_name IN (
                'k8s_pod_name', 'k8s_namespace', 'access_url',
                'gcs_home_prefix', 'last_activity_at'
            )
            ORDER BY column_name
        """)
    )
    columns = {row[0]: row[1] for row in result.fetchall()}

    assert "k8s_pod_name" in columns
    assert "k8s_namespace" in columns
    assert "access_url" in columns
    assert "gcs_home_prefix" in columns
    assert "last_activity_at" in columns

    assert columns["k8s_pod_name"] == "character varying"
    assert columns["k8s_namespace"] == "character varying"
    assert columns["access_url"] == "character varying"
    assert columns["gcs_home_prefix"] == "character varying"
    assert "timestamp" in columns["last_activity_at"]


@pytest.mark.asyncio
async def test_platform_config_accepts_notebook_keys(session):
    """Platform config table accepts the notebook-related keys from migration 028."""
    # Simulate what the migration does: insert platform_config keys
    await session.execute(
        text("""
            INSERT INTO platform_config (key, value) VALUES
                ('k8s_notebook_namespace', 'bioaf-notebooks'),
                ('notebook_idle_timeout_hours', '4'),
                ('notebook_idle_warning_minutes', '15'),
                ('bioaf_scrna_image', 'null'),
                ('artifact_registry_repo', 'null')
            ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.flush()

    result = await session.execute(
        text("""
            SELECT key, value FROM platform_config
            WHERE key IN (
                'k8s_notebook_namespace',
                'notebook_idle_timeout_hours',
                'notebook_idle_warning_minutes',
                'bioaf_scrna_image',
                'artifact_registry_repo'
            )
            ORDER BY key
        """)
    )
    rows = {row[0]: row[1] for row in result.fetchall()}

    assert rows["k8s_notebook_namespace"] == "bioaf-notebooks"
    assert rows["notebook_idle_timeout_hours"] == "4"
    assert rows["notebook_idle_warning_minutes"] == "15"
    assert rows["bioaf_scrna_image"] == "null"
    assert rows["artifact_registry_repo"] == "null"
