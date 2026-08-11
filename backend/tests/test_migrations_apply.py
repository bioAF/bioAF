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

from .conftest import TEST_DATABASE_URL

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

# Its own schema, so applying 100+ migrations cannot disturb the create_all
# tables the rest of the suite is using in `public` (or in a per-xdist-worker
# schema). Named per worker for the same reason.
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
    return SCHEMA if worker_id == "master" else f"{SCHEMA}_{worker_id}"


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
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {schema}"))

    cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    # env.py would otherwise build its own engine from settings.database_url and
    # migrate whatever database this developer happens to point at.
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    async with engine.connect() as conn:

        def _upgrade(sync_conn):
            cfg.attributes["connection"] = sync_conn
            command.upgrade(cfg, "head")

        await conn.run_sync(_upgrade)
        await conn.commit()

    async with engine.connect() as conn:
        yield conn, schema

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
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
