"""Migration 133: the installation metrics outbox, and fresh-versus-existing classification (ADR-075).

The migration classifies the database before bootstrap can create an Organization: a database that
already has an Organization, an admin, or a completed setup is an existing installation. It makes no
choice for the admin and reports nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.plugins import invocation

from .conftest import TEST_DATABASE_URL

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


@pytest_asyncio.fixture
async def stepped(worker_id):
    """An empty schema and a function that migrates it to a revision; yields (engine, upgrade)."""
    schema = invocation.claim_schema("metrics_migration", worker_id)
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {invocation.quote(schema)}"))

    cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    async def upgrade(revision: str) -> None:
        async with engine.connect() as conn:

            def _run(sync_conn):
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_logging_config"] = True
                command.upgrade(cfg, revision)

            await conn.run_sync(_run)
            await conn.commit()

    yield engine, upgrade

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
    await engine.dispose()


async def _state(engine) -> dict:
    async with engine.connect() as conn:
        raw = (
            await conn.execute(text("SELECT value FROM platform_config WHERE key = 'install_metrics_state'"))
        ).scalar_one()
        events = (await conn.execute(text("SELECT count(*) FROM install_metric_events"))).scalar_one()
    return {**json.loads(raw), "events": events}


@pytest.mark.asyncio
async def test_a_fresh_database_is_a_new_installation_with_no_choice(stepped):
    engine, upgrade = stepped
    await upgrade("head")

    state = await _state(engine)

    assert state["classification"] == "new"
    assert state["preference"] == "pending"
    assert state["installation_id"] is None
    assert state["setup_completed"] is False
    assert state["events"] == 0


@pytest.mark.asyncio
async def test_a_completed_setup_is_an_existing_installation(stepped):
    engine, upgrade = stepped
    await upgrade("132")
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO organizations (name, setup_complete) VALUES ('Lab', true)"))
    await upgrade("head")

    state = await _state(engine)

    assert state["classification"] == "existing"
    assert state["setup_completed"] is True
    assert state["preference"] == "pending"
    assert state["installation_id"] is None
    assert state["events"] == 0


@pytest.mark.asyncio
async def test_an_organization_mid_setup_is_an_existing_installation(stepped):
    engine, upgrade = stepped
    await upgrade("132")
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO organizations (name, setup_complete) VALUES ('Lab', false)"))
    await upgrade("head")

    state = await _state(engine)

    assert state["classification"] == "existing"
    assert state["setup_completed"] is False


@pytest.mark.asyncio
async def test_rerunning_the_classification_keeps_a_recorded_choice(stepped):
    engine, upgrade = stepped
    await upgrade("head")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE platform_config SET value = jsonb_set(value::jsonb, '{preference}', '\"disabled\"')::text "
                "WHERE key = 'install_metrics_state'"
            )
        )
    await upgrade("132")
    await upgrade("head")

    assert (await _state(engine))["preference"] == "disabled"
