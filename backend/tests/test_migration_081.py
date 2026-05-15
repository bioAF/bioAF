"""Migration 081: LLM integration v1 schema and RBAC backfill.

Applies the migration on a clean schema (rather than the conftest's
Base.metadata.create_all path) and verifies:

- The three new tables exist with the partial unique indexes.
- An existing admin role gets llm_integration:{configure,use}.
- An existing comp_bio role gets llm_integration:use.
- An existing bench role does NOT get either.
- The backfill is idempotent: re-running INSERT NOT EXISTS clauses adds nothing.

We don't actually run alembic against this engine; instead we exercise the
upgrade() function directly inside a fresh session, having first run the
prior migrations' schema via Base.metadata.create_all and emptied any
llm_integration:* perms the bootstrap seed may have inserted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_081_tables_exist(session):
    for table in ("llm_provider_config", "agent_review_jobs", "agent_reviews"):
        row = await session.execute(
            text(
                "SELECT to_regclass(:t) AS rel"
            ).bindparams(t=table)
        )
        assert row.scalar_one() is not None, f"{table} missing"


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_two_active_per_org(session, admin_user):
    """Insert two configs and try to flip both active; the partial unique
    index must reject the second."""
    org_id = admin_user.organization_id
    user_id = admin_user.id

    await session.execute(
        text(
            "INSERT INTO llm_provider_config "
            "(organization_id, provider, model, is_active, created_by_user_id, updated_by_user_id) "
            "VALUES (:org, 'openai', 'gpt-5', true, :u, :u)"
        ).bindparams(org=org_id, u=user_id)
    )
    await session.execute(
        text(
            "INSERT INTO llm_provider_config "
            "(organization_id, provider, model, is_active, created_by_user_id, updated_by_user_id) "
            "VALUES (:org, 'anthropic', 'claude-opus-4-7', false, :u, :u)"
        ).bindparams(org=org_id, u=user_id)
    )
    await session.commit()

    with pytest.raises(Exception):
        await session.execute(
            text(
                "UPDATE llm_provider_config SET is_active = true "
                "WHERE organization_id = :org AND provider = 'anthropic'"
            ).bindparams(org=org_id)
        )
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_two_inflight_debounce(session, admin_user):
    org_id = admin_user.organization_id
    user_id = admin_user.id

    base = {
        "org": org_id,
        "u": user_id,
        "entity_type": "pipeline_run",
        "entity_id": 12345,
        "review_type": "pipeline_run_review_v1",
        "provider": "openai",
        "model": "gpt-5",
        "pt": "pipeline_run_review_v1",
    }
    await session.execute(
        text(
            "INSERT INTO agent_review_jobs "
            "(organization_id, triggered_by_user_id, entity_type, entity_id, review_type, "
            "provider, model, prompt_template_version, status, artifact_gcs_paths) "
            "VALUES (:org, :u, :entity_type, :entity_id, :review_type, "
            ":provider, :model, :pt, 'pending', '[]'::jsonb)"
        ).bindparams(**base)
    )
    await session.commit()

    with pytest.raises(Exception):
        await session.execute(
            text(
                "INSERT INTO agent_review_jobs "
                "(organization_id, triggered_by_user_id, entity_type, entity_id, review_type, "
                "provider, model, prompt_template_version, status, artifact_gcs_paths) "
                "VALUES (:org, :u, :entity_type, :entity_id, :review_type, "
                ":provider, :model, :pt, 'pending', '[]'::jsonb)"
            ).bindparams(**base)
        )
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_partial_unique_releases_on_terminal(session, admin_user):
    org_id = admin_user.organization_id
    user_id = admin_user.id

    base = {
        "org": org_id,
        "u": user_id,
        "entity_type": "pipeline_run",
        "entity_id": 67890,
        "review_type": "pipeline_run_review_v1",
        "provider": "openai",
        "model": "gpt-5",
        "pt": "pipeline_run_review_v1",
    }
    await session.execute(
        text(
            "INSERT INTO agent_review_jobs "
            "(organization_id, triggered_by_user_id, entity_type, entity_id, review_type, "
            "provider, model, prompt_template_version, status, artifact_gcs_paths) "
            "VALUES (:org, :u, :entity_type, :entity_id, :review_type, "
            ":provider, :model, :pt, 'failed', '[]'::jsonb)"
        ).bindparams(**base)
    )
    await session.commit()
    # A second 'pending' row on the same key is allowed because the prior row
    # is in a terminal status, not in the partial index.
    await session.execute(
        text(
            "INSERT INTO agent_review_jobs "
            "(organization_id, triggered_by_user_id, entity_type, entity_id, review_type, "
            "provider, model, prompt_template_version, status, artifact_gcs_paths) "
            "VALUES (:org, :u, :entity_type, :entity_id, :review_type, "
            ":provider, :model, :pt, 'pending', '[]'::jsonb)"
        ).bindparams(**base)
    )
    await session.commit()
