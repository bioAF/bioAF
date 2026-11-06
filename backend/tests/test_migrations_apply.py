"""The migration chain actually runs, and lands on the schema the models declare.

Everything else in this suite gets its tables from `Base.metadata.create_all`
(see conftest.py), which is not how any real database here is built: every
install and every deploy runs `alembic upgrade head`. That gap meant the suite
could be entirely green while the migration chain was broken, because nothing
executed it. A migration could fail to apply, be non-idempotent, or drift from
the models, and the first place anyone found out was a deploy.

These tests close that. They run the real chain against a throwaway schema and
compare the result to `Base.metadata`, so a migration that does not apply, or
applies but disagrees with the models, fails here instead of in production.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
import app.models  # noqa: F401 -- registers every model on Base.metadata

from tests.plugins import invocation

from .conftest import TEST_DATABASE_URL

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

# Its own schema, so applying 100+ migrations cannot disturb the create_all
# tables the rest of the suite is using. Named per invocation and worker for the
# same reason.
SCHEMA = "migration_check"

# Drift categories that mean the models and the migrations genuinely disagree.
# compare_metadata also emits lower-signal diffs (server defaults, type
# variants, index naming) that differ between a create_all schema and a
# migrated one without anything being broken; asserting on those would make
# this test fail for reasons nobody needs to act on.
STRUCTURAL_DIFFS = {"add_table", "remove_table", "add_column", "remove_column"}

# Drift that already existed when this test was written: schema the migration
# chain still builds but no model declares any more. `remove_*` reads as "the
# database has it, the models do not".
#
#   batches            superseded by sample_batches + sequencing_batches. Its
#                      model file (app/models/batch.py) is not imported by
#                      app/models/__init__.py, so it is dead either way.
#   *.github_repo_name  columns on experiments/projects; the live one lives on
#                      gitops_repos.
#   samples.sample_id_external / samples.batch_id
#                      left over from the batch split above.
#
# Recorded rather than fixed: dropping a table or column from a production
# database destroys data and is the owner's call, not a side effect of adding a
# test. The point of listing them is that anything NOT on this list fails, so
# new drift is caught the day it appears.
KNOWN_DRIFT = {
    ("remove_table", "batches"),
    ("remove_column", "experiments", "github_repo_name"),
    ("remove_column", "projects", "github_repo_name"),
    ("remove_column", "samples", "sample_id_external"),
    ("remove_column", "samples", "batch_id"),
}


def _drift_key(diff) -> tuple | None:
    """Reduce an autogenerate op to (kind, table[, column]), or None if unhandled."""
    kind = diff[0]
    if kind in {"add_table", "remove_table"}:
        return (kind, diff[1].name)
    if kind in {"add_column", "remove_column"}:
        return (kind, diff[2], diff[3].name)
    return None


def _schema_for(worker_id: str) -> str:
    """``migration_check_<invocation>_<worker>``: unique per invocation and worker, recorded for cleanup."""
    return invocation.claim_schema(SCHEMA, worker_id)


@pytest_asyncio.fixture
async def migrated_schema(worker_id):
    """Run `alembic upgrade head` into an empty schema; yield a connection to it."""
    schema = _schema_for(worker_id)

    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )

    async with engine.begin() as conn:
        # Start from nothing, so this measures the chain and not leftovers.
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {invocation.quote(schema)}"))

    cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    # env.py would otherwise build its own engine from settings.database_url and
    # migrate whatever database this developer happens to point at.
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    async with engine.connect() as conn:

        def _upgrade(sync_conn):
            cfg.attributes["connection"] = sync_conn
            # Leave pytest's logging alone; see test_running_migrations_leaves_test_logging_alone.
            cfg.attributes["skip_logging_config"] = True
            command.upgrade(cfg, "head")

        await conn.run_sync(_upgrade)
        await conn.commit()

    async with engine.connect() as conn:
        yield conn, schema

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
    await engine.dispose()


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_migration_chain_applies_to_an_empty_database(migrated_schema):
    """`alembic upgrade head` runs clean from nothing.

    The fixture doing the upgrade is the assertion: a migration that raises
    fails here. This then checks the run was real rather than a no-op that
    stamped a version and created nothing.
    """
    conn, schema = migrated_schema

    tables = (
        (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                {"s": schema},
            )
        )
        .scalars()
        .all()
    )

    assert "alembic_version" in tables, "chain did not stamp a version"
    # Guard-the-guard: without this, a chain that silently created nothing
    # would satisfy every other assertion in this file.
    assert len(tables) > 50, f"expected the full schema, got {len(tables)} tables: {sorted(tables)}"


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_migrated_schema_matches_the_models(migrated_schema):
    """No table or column drift between the migration chain and Base.metadata.

    This is the failure the old text-matching migration tests could never see:
    a model gains a column and no migration adds it, so create_all-backed tests
    pass while production is missing the column.
    """
    conn, schema = migrated_schema

    def _diff(sync_conn):
        ctx = MigrationContext.configure(
            sync_conn,
            opts={"target_metadata": Base.metadata, "include_schemas": False},
        )
        return compare_metadata(ctx, Base.metadata)

    diffs = await conn.run_sync(_diff)

    observed = {
        key
        for d in diffs
        if isinstance(d, tuple) and d and d[0] in STRUCTURAL_DIFFS
        for key in [_drift_key(d)]
        if key is not None
    }

    unexpected = sorted(observed - KNOWN_DRIFT)
    assert unexpected == [], (
        "migrations and models disagree on tables/columns:\n"
        + "\n".join(f"  {k}" for k in unexpected)
        + "\n\nEither add the migration that makes the database match the models, "
        "or record it in KNOWN_DRIFT with a reason."
    )


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_known_drift_list_has_no_stale_entries(migrated_schema):
    """KNOWN_DRIFT describes reality, so a cleaned-up entry must be deleted from it.

    Without this, the allowlist silently outlives the drift it excuses and
    starts hiding regressions that reintroduce the same column.
    """
    conn, _ = migrated_schema

    def _diff(sync_conn):
        ctx = MigrationContext.configure(
            sync_conn,
            opts={"target_metadata": Base.metadata, "include_schemas": False},
        )
        return compare_metadata(ctx, Base.metadata)

    diffs = await conn.run_sync(_diff)
    observed = {
        key
        for d in diffs
        if isinstance(d, tuple) and d and d[0] in STRUCTURAL_DIFFS
        for key in [_drift_key(d)]
        if key is not None
    }

    stale = sorted(KNOWN_DRIFT - observed)
    assert stale == [], "KNOWN_DRIFT lists drift that no longer exists; remove:\n" + "\n".join(f"  {k}" for k in stale)


# Created at import, so it exists before the fixture runs the migration chain.
_APP_LOGGER = logging.getLogger("bioaf.migration_logging_probe")


@pytest.fixture
def root_capture_handler():
    """A handler on the root logger before the upgrade, standing in for caplog's."""
    handler = logging.NullHandler()
    logging.getLogger().addHandler(handler)
    yield handler
    logging.getLogger().removeHandler(handler)


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_running_migrations_leaves_test_logging_alone(root_capture_handler, migrated_schema):
    """env.py's fileConfig must not run inside the suite.

    It disables every logger created before it and replaces the root handlers,
    so any later test asserting on a `bioaf.*` log line saw nothing and failed
    depending on which xdist worker it shared with this file.
    """
    assert not _APP_LOGGER.disabled
    assert root_capture_handler in logging.getLogger().handlers


# --- rules beyond tables and columns (plan 4 stage 5, shared with plan 9) -------------------
#
# Compared through the catalogs by what they enforce, not by name. Each entry is drift that
# exists today; anything not listed fails, and a listed entry that disappears fails too.

# Uniqueness only the migration chain enforces: every create_all-backed test runs without these.
UNIQUE_ONLY_IN_MIGRATIONS = {
    ("experiments", "btree (organization_id, external_id) WHERE (external_id IS NOT NULL)"),
    ("lab_glossary_terms", "btree (organization_id, lower((term)::text))"),
    ("notification_preferences", "btree (user_id, event_type, channel)"),
    ("notification_rules", "btree (organization_id, event_type, channel)"),
    ("pipeline_catalog", "btree (organization_id, pipeline_key)"),
    ("projects", "btree (organization_id, external_id) WHERE (external_id IS NOT NULL)"),
    # Migration 080.
    ("samples", "btree (experiment_id, external_id) WHERE (external_id IS NOT NULL)"),
    (
        "samples",
        "btree (sequencing_batch_id, sequencing_batch_position) "
        "WHERE ((sequencing_batch_id IS NOT NULL) AND (sequencing_batch_position IS NOT NULL))",
    ),
    ("session_credentials", "btree (organization_id, username)"),
}

# NOT NULL only the models declare: production accepts NULL here, create_all-backed tests do not.
NOT_NULL_ONLY_IN_MODELS = {
    ("custom_pipeline_versions", "created_at"),
    ("custom_pipelines", "created_at"),
    ("custom_pipelines", "updated_at"),
    ("entity_snapshots", "created_at"),
    ("environment_versions", "created_at"),
    ("environments", "created_at"),
    ("environments", "updated_at"),
    ("experiment_auto_runs", "created_at"),
    ("experiment_auto_runs", "updated_at"),
    ("manifest_entries", "created_at"),
    ("notebook_session_files", "access_type"),
    ("notebook_session_files", "accessed_at"),
    ("notebook_session_files", "file_id"),
    ("notebook_session_files", "session_id"),
    ("pending_auto_runs", "created_at"),
    ("pipeline_run_input_files", "created_at"),
    ("platform_config", "value"),
    ("roles", "created_at"),
    ("sample_batches", "created_at"),
    ("sample_batches", "updated_at"),
    ("samplesheet_mappings", "created_at"),
    ("samplesheet_mappings", "updated_at"),
    ("sequencing_batches", "created_at"),
    ("sequencing_batches", "updated_at"),
    ("session_credentials", "created_at"),
    ("session_credentials", "updated_at"),
    ("slack_channel_mappings", "created_at"),
    ("slack_installations", "created_at"),
}

# Rows an empty-schema upgrade leaves behind. All global; migrations that backfill per
# Organization (roles in 036, permissions in later ones) find no Organization and insert nothing,
# so roles come only from seed_builtin_roles when a test or install creates an Organization.
MIGRATION_SEEDS = {"component_states": 8, "controlled_vocabularies": 67, "platform_config": 42}


@pytest_asyncio.fixture
async def create_all_schema(worker_id):
    """The schema every other test in the suite uses, built the same way, in its own namespace."""
    schema = invocation.claim_schema("createall_check", worker_id)
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {invocation.quote(schema)}"))
        await conn.run_sync(Base.metadata.create_all)
    yield schema
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
    await engine.dispose()


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_constraint_drift_between_migrations_and_models_is_exactly_the_recorded_drift(
    migrated_schema, create_all_schema
):
    """Uniqueness, foreign keys, checks and NOT NULL agree except for the recorded entries."""
    from tests.support import schema_catalog

    conn, schema = migrated_schema
    migrated = await schema_catalog.read(conn, schema, count_rows=False)
    created = await schema_catalog.read(conn, create_all_schema, count_rows=False)
    diff = schema_catalog.differences(migrated, created)

    assert set(diff["unique"]["migrations_only"]) == UNIQUE_ONLY_IN_MIGRATIONS
    assert diff["unique"]["models_only"] == []
    assert diff["foreign_keys"] == {"migrations_only": [], "models_only": []}
    assert diff["checks"] == {"migrations_only": [], "models_only": []}
    assert diff["not_null"]["migrations_only"] == []
    assert set(diff["not_null"]["models_only"]) == NOT_NULL_ONLY_IN_MODELS


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_an_empty_schema_upgrade_seeds_only_global_rows(migrated_schema):
    from tests.support import schema_catalog

    conn, schema = migrated_schema
    catalog = await schema_catalog.read(conn, schema)
    assert catalog["seeded"] == MIGRATION_SEEDS
    assert "roles" not in catalog["seeded"], "roles are per Organization; seed_builtin_roles owns them"


async def _org_and_experiment(conn) -> int:
    org = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('o') RETURNING id"))).scalar_one()
    project = (
        await conn.execute(
            text("INSERT INTO projects (name, organization_id, status) VALUES ('p', :o, 'active') RETURNING id"),
            {"o": org},
        )
    ).scalar_one()
    return (
        await conn.execute(
            text(
                "INSERT INTO experiments (name, organization_id, project_id, status) "
                "VALUES ('e', :o, :p, 'registered') RETURNING id"
            ),
            {"o": org, "p": project},
        )
    ).scalar_one()


async def _add_sample(conn, experiment_id: int, external_id: str | None) -> None:
    await conn.execute(
        text("INSERT INTO samples (experiment_id, external_id, status) VALUES (:e, :x, 'registered')"),
        {"e": experiment_id, "x": external_id},
    )


@pytest.mark.skipif(
    os.environ.get("BIOAF_SKIP_MIGRATION_TESTS") == "1",
    reason="explicitly disabled",
)
@pytest.mark.asyncio
async def test_migration_080_refuses_a_repeated_external_id_within_one_experiment(migrated_schema):
    from sqlalchemy.exc import IntegrityError

    conn, _ = migrated_schema
    experiment = await _org_and_experiment(conn)
    other = await _org_and_experiment(conn)
    await _add_sample(conn, experiment, "S-1")
    await _add_sample(conn, other, "S-1")  # another experiment may reuse it
    await _add_sample(conn, experiment, None)
    await _add_sample(conn, experiment, None)  # the index is partial: unnamed samples never collide
    await conn.commit()

    with pytest.raises(IntegrityError, match="uq_samples_experiment_external_id"):
        await _add_sample(conn, experiment, "S-1")

    # The caller recovers: after rolling back, the connection is usable and earlier work is intact.
    await conn.rollback()
    await _add_sample(conn, experiment, "S-2")
    await conn.commit()
    names = (
        await conn.execute(
            text("SELECT external_id FROM samples WHERE experiment_id = :e ORDER BY external_id"),
            {"e": experiment},
        )
    ).scalars()
    assert list(names) == ["S-1", "S-2", None, None]
