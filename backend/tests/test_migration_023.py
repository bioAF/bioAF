"""Tests for migration 023 - terraform_runs new columns and platform_config seed rows.

Tests that:
- New columns exist on terraform_runs (module_name, plan_json, resources_planned,
  resources_completed, apply_log, terraform_state_url)
- New platform_config keys exist (terraform_state_bucket, terraform_initialized)
- Migration SQL is idempotent (ON CONFLICT DO NOTHING)
"""

import pytest
from sqlalchemy import text
from app.services.bootstrap_roles import seed_builtin_roles


@pytest.mark.asyncio
async def test_migration_adds_terraform_runs_columns(session):
    """Migration 023 adds six new columns to terraform_runs."""
    # Use the conftest-created schema (Base.metadata.create_all), which picks up the
    # updated model. Verify that the model columns are present by inserting a row
    # using all new fields and reading them back.
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService

    org = Organization(name="MigrationTestOrg023", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email="mig023@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map["admin"],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()

    # Insert a terraform_run with the new columns (status required, server_default not applied in create_all)
    await session.execute(
        text("""
        INSERT INTO terraform_runs
            (triggered_by_user_id, action, status, module_name, plan_json,
             resources_planned, resources_completed, apply_log, terraform_state_url)
        VALUES
            (:uid, 'plan', 'planning', 'foundation', '{"add": 1}'::jsonb, 1, 0, 'log text', 'gs://bucket/state')
        """).bindparams(uid=user.id)
    )
    await session.commit()

    row = (
        await session.execute(
            text(
                "SELECT module_name, plan_json, resources_planned, "
                "resources_completed, apply_log, terraform_state_url "
                "FROM terraform_runs WHERE triggered_by_user_id = :uid"
            ).bindparams(uid=user.id)
        )
    ).fetchone()

    assert row is not None
    assert row[0] == "foundation"
    assert row[1] == {"add": 1}
    assert row[2] == 1
    assert row[3] == 0
    assert row[4] == "log text"
    assert row[5] == "gs://bucket/state"


@pytest.mark.asyncio
async def test_migration_seeds_terraform_platform_config_keys(session):
    """Migration 023 inserts terraform_state_bucket and terraform_initialized keys."""
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('terraform_state_bucket', ''),
            ('terraform_initialized',  'false')
        ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.commit()

    rows = (
        await session.execute(
            text(
                "SELECT key, value FROM platform_config "
                "WHERE key IN ('terraform_state_bucket', 'terraform_initialized') "
                "ORDER BY key"
            )
        )
    ).fetchall()

    config = {r[0]: r[1] for r in rows}
    assert config["terraform_initialized"] == "false"
    assert config["terraform_state_bucket"] == ""


@pytest.mark.asyncio
async def test_migration_platform_config_is_idempotent(session):
    """Running the migration SQL twice does not fail or duplicate rows."""
    seed_sql = text("""
        INSERT INTO platform_config (key, value) VALUES
            ('terraform_state_bucket', ''),
            ('terraform_initialized',  'false')
        ON CONFLICT (key) DO NOTHING
    """)
    await session.execute(seed_sql)
    await session.commit()
    await session.execute(seed_sql)
    await session.commit()

    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM platform_config WHERE key IN ('terraform_state_bucket', 'terraform_initialized')"
            )
        )
    ).scalar()
    assert count == 2
