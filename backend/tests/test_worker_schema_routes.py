"""Pilot fixture routes on a migration-built worker schema (plan 4, stage 5).

``migrated_reset``: the worker's migrated schema is reset to the empty-upgrade
baseline before each test (tables truncated, sequences restarted, migration
seed rows and their sequences restored). Real commits stay real.

``rollback``: the test runs inside one outer transaction on one connection;
the session and every redirected session factory join it with savepoints, and
it is rolled back at the end.

Files not on a pilot route keep the existing per-test create_all schema.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.plugins.db_url import database_url
from tests.support import fixture_routes, worker_schema

MIGRATION_SEEDS = {"component_states": 8, "controlled_vocabularies": 67, "platform_config": 42}


@pytest.fixture(autouse=True)
def _no_route_overrides_from_the_outer_run(monkeypatch):
    """These tests set the route switches themselves; a whole-suite override must not leak in."""
    monkeypatch.delenv("BIOAF_FIXTURE_ROUTES", raising=False)
    monkeypatch.delenv("BIOAF_FIXTURE_TRIAL", raising=False)


async def _counts(conn, schema: str) -> dict[str, int]:
    tables = (
        await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = :s AND tablename <> 'alembic_version'"),
            {"s": schema},
        )
    ).scalars()
    found = {}
    for t in tables:
        n = (await conn.execute(text(f'SELECT count(*) FROM "{schema}"."{t}"'))).scalar_one()
        if n:
            found[t] = n
    return found


@pytest.fixture
def scratch_worker_schema(worker_id):
    """A worker schema of this test's own, so reset tests cannot disturb the shared one."""
    ws = worker_schema.build("routecheck", worker_id)
    yield ws
    worker_schema.drop(ws)


# --- the committed-state reset --------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_restores_the_empty_upgrade_baseline_after_real_commits(scratch_worker_schema):
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with engine.begin() as conn:
            assert await _counts(conn, ws.schema) == MIGRATION_SEEDS
            await conn.execute(text("INSERT INTO organizations (name) VALUES ('a'), ('b')"))
            await conn.execute(text("DELETE FROM platform_config"))
            await conn.execute(
                text("INSERT INTO controlled_vocabularies (field_name, allowed_value) VALUES ('f', 'v')")
            )

        await worker_schema.reset(engine, ws)

        async with engine.begin() as conn:
            assert await _counts(conn, ws.schema) == MIGRATION_SEEDS
            # Identity restarts where an empty upgrade leaves it: the first Organization is id 1 again,
            # and a seeded table continues after its seed rows instead of colliding with them.
            org = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('c') RETURNING id"))).scalar_one()
            assert org == 1
            await conn.execute(
                text("INSERT INTO controlled_vocabularies (field_name, allowed_value) VALUES ('f', 'w')")
            )
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reset_rebuilds_when_a_test_changed_the_schema(scratch_worker_schema):
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE stray (id int)"))
            await conn.execute(text("DROP TABLE lab_document_tags CASCADE"))

        await worker_schema.reset(engine, ws)

        async with engine.begin() as conn:
            tables = set(
                (
                    await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": ws.schema})
                ).scalars()
            )
        assert "stray" not in tables
        assert "lab_document_tags" in tables
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE organizations ADD COLUMN stray int",
        "ALTER TABLE organizations ALTER COLUMN name DROP NOT NULL",
        "ALTER TABLE organizations ALTER COLUMN name SET DEFAULT 'x'",
        "ALTER TABLE organizations ALTER COLUMN name TYPE text",
        "CREATE INDEX stray_idx ON organizations (name)",
        "ALTER TABLE organizations ADD CONSTRAINT stray_check CHECK (length(name) > 0)",
        "CREATE FUNCTION stray_fn() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END' ;;"
        " CREATE TRIGGER stray_trg BEFORE INSERT ON organizations FOR EACH ROW EXECUTE FUNCTION stray_fn()",
        "CREATE SEQUENCE stray_seq",
        "CREATE VIEW stray_view AS SELECT 1 AS one",
        "CREATE TYPE stray_kind AS ENUM ('a')",
    ],
)
async def test_reset_rebuilds_when_a_test_changed_a_table_without_renaming_one(scratch_worker_schema, ddl):
    """Columns, defaults, types, indexes, constraints, triggers and other objects count, not only table names."""
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with engine.connect() as conn:
            before = await worker_schema.fingerprint(conn, ws.schema)
        async with engine.begin() as conn:
            for statement in ddl.split(" ;; "):
                await conn.execute(text(statement))
        async with engine.connect() as conn:
            assert await worker_schema.fingerprint(conn, ws.schema) != before

        ws = await worker_schema.reset(engine, ws)

        async with engine.connect() as conn:
            assert await worker_schema.fingerprint(conn, ws.schema) == before
            stray = await conn.execute(
                text(
                    "SELECT 1 FROM pg_class WHERE relnamespace = CAST(:s AS regnamespace) AND relname LIKE 'stray%'"
                    " UNION ALL SELECT 1 FROM pg_attribute WHERE attrelid = 'organizations'::regclass"
                    " AND attname = 'stray'"
                ),
                {"s": ws.schema},
            )
            assert stray.all() == []
    finally:
        await engine.dispose()


# --- routing ------------------------------------------------------------------------------


def test_routes_come_from_the_pilot_lists():
    assert fixture_routes.route_for("test_worker_schema_routes.py") == "existing"
    for name in fixture_routes.MIGRATED_RESET_PILOT:
        assert fixture_routes.route_for(name) == "migrated_reset"
    for name in fixture_routes.ROLLBACK_PILOT:
        assert fixture_routes.route_for(name) == "rollback"


def test_the_existing_route_can_be_forced_for_comparisons(monkeypatch):
    monkeypatch.setenv("BIOAF_FIXTURE_ROUTES", "existing")
    for name in fixture_routes.MIGRATED_RESET_PILOT | fixture_routes.ROLLBACK_PILOT:
        assert fixture_routes.route_for(name) == "existing"


def test_pilots_only_take_files_the_inventory_allows():
    """A real-commit file never goes through rollback; schema-changing files go nowhere."""
    import json
    from pathlib import Path

    inventory = json.loads((Path(__file__).parent / "isolation_inventory.json").read_text())["files"]
    for name in fixture_routes.ROLLBACK_PILOT:
        assert inventory[name]["category"] == "single_connection", name
    for name in fixture_routes.MIGRATED_RESET_PILOT:
        assert inventory[name]["category"] in {"real_commit", "single_connection"}, name


# --- the rollback route ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_route_keeps_commits_visible_inside_and_discards_them_after(scratch_worker_schema):
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with worker_schema.rollback_scope(engine, ws) as factory:
            async with factory() as first:
                await first.execute(text("INSERT INTO organizations (name) VALUES ('inside')"))
                await first.commit()  # releases a savepoint; the outer transaction stays open
            async with factory() as second:  # another "session", same connection: sees the commit
                names = (await second.execute(text("SELECT name FROM organizations"))).scalars().all()
            assert names == ["inside"]

            # A savepoint rolled back inside the test still discards only its own work.
            async with factory() as third:
                await third.execute(text("INSERT INTO organizations (name) VALUES ('dropped')"))
                await third.rollback()
            async with factory() as fourth:
                names = (await fourth.execute(text("SELECT name FROM organizations"))).scalars().all()
            assert names == ["inside"]

        outside = async_sessionmaker(engine, class_=AsyncSession)
        async with outside() as reader:
            assert (await reader.execute(text("SELECT count(*) FROM organizations"))).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_consecutive_rollback_scopes_start_from_the_same_sequence_positions(scratch_worker_schema):
    """Sequences are not transactional: a scope must put back what its rolled-back inserts consumed."""
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        ids = []
        for _ in range(2):
            async with worker_schema.rollback_scope(engine, ws) as factory:
                async with factory() as s:
                    ids.append(
                        (
                            await s.execute(text("INSERT INTO organizations (name) VALUES ('o') RETURNING id"))
                        ).scalar_one()
                    )
                    await s.commit()
        assert ids == [1, 1]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_route_publishes_post_commit_events_for_the_session_commit(scratch_worker_schema):
    """The test session's commit is its outermost transaction, so after-commit hooks still fire."""
    from app.services.event_bus import event_bus
    from app.services.post_commit_events import defer_event, wait_for_deferred_events

    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    seen: list[dict] = []

    async def _record(payload):
        seen.append(payload)

    event_bus.subscribe("route.probe", _record)
    try:
        async with worker_schema.rollback_scope(engine, ws) as factory:
            async with factory() as s:
                defer_event(s, "route.probe", {"n": 1})
                await s.commit()
            await wait_for_deferred_events()
        assert seen == [{"n": 1}]
    finally:
        await engine.dispose()


def test_a_trial_routes_whole_inventory_categories(monkeypatch):
    """BIOAF_FIXTURE_TRIAL characterizes candidates without editing the pilot lists."""
    import json
    from pathlib import Path

    inventory = json.loads((Path(__file__).parent / "isolation_inventory.json").read_text())["files"]
    single = next(n for n, e in inventory.items() if e["category"] == "single_connection")
    real = next(n for n, e in inventory.items() if e["category"] == "real_commit")
    schema = next(n for n, e in inventory.items() if e["category"] == "schema")

    monkeypatch.setenv("BIOAF_FIXTURE_TRIAL", "rollback=single_connection;migrated_reset=real_commit")
    assert fixture_routes.route_for(single) == "rollback"
    assert fixture_routes.route_for(real) == "migrated_reset"
    assert fixture_routes.route_for(schema) == "existing"


def test_a_trial_cannot_send_real_commit_files_through_rollback(monkeypatch):
    monkeypatch.setenv("BIOAF_FIXTURE_TRIAL", "rollback=real_commit")
    with pytest.raises(ValueError, match="rollback"):
        fixture_routes.route_for("test_auth.py")


@pytest.mark.asyncio
async def test_reset_clears_parent_and_child_rows_and_restarts_only_what_moved(scratch_worker_schema):
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with engine.begin() as conn:
            org = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('o') RETURNING id"))).scalar_one()
            project = (
                await conn.execute(
                    text(
                        "INSERT INTO projects (name, organization_id, status) VALUES ('p', :o, 'active') RETURNING id"
                    ),
                    {"o": org},
                )
            ).scalar_one()
            await conn.execute(
                text("INSERT INTO experiments (name, organization_id, project_id, status) VALUES ('e', :o, :p, 'x')"),
                {"o": org, "p": project},
            )
            # A row inserted and deleted again still moves the sequence; reset must restart it.
            await conn.execute(
                text("INSERT INTO roles (name, organization_id, is_system) VALUES ('r', :o, true)"), {"o": org}
            )
            await conn.execute(text("DELETE FROM roles"))

        await worker_schema.reset(engine, ws)

        async with engine.begin() as conn:
            assert await _counts(conn, ws.schema) == MIGRATION_SEEDS
            org = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('o') RETURNING id"))).scalar_one()
            role = (
                await conn.execute(
                    text("INSERT INTO roles (name, organization_id, is_system) VALUES ('r', :o, true) RETURNING id"),
                    {"o": org},
                )
            ).scalar_one()
            assert (org, role) == (1, 1)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reset_falls_back_to_truncating_everything_when_targeted_deletes_fail(scratch_worker_schema, monkeypatch):
    ws = scratch_worker_schema
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": ws.schema}})
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO organizations (name) VALUES ('o')"))
        # Parents before children: the targeted DELETE violates a foreign key.
        monkeypatch.setattr(ws, "delete_order", tuple(reversed(ws.delete_order)))
        async with engine.begin() as conn:
            org = (await conn.execute(text("SELECT id FROM organizations"))).scalar_one()
            await conn.execute(
                text("INSERT INTO projects (name, organization_id, status) VALUES ('p', :o, 'active')"), {"o": org}
            )

        await worker_schema.reset(engine, ws)

        async with engine.begin() as conn:
            assert await _counts(conn, ws.schema) == MIGRATION_SEEDS
        assert worker_schema.TIMINGS["fallback"], "the fallback was not taken"
    finally:
        await engine.dispose()


def test_files_that_failed_on_a_migrated_route_stay_on_the_existing_one():
    pilots = fixture_routes.MIGRATED_RESET_PILOT | fixture_routes.ROLLBACK_PILOT
    assert not (fixture_routes.KEPT_EXISTING & pilots)
    assert not (fixture_routes.MIGRATED_RESET_PILOT & fixture_routes.ROLLBACK_PILOT)
    for name in fixture_routes.KEPT_EXISTING:
        assert fixture_routes.route_for(name) == "existing"


def test_seed_assuming_files_stay_on_the_existing_route():
    pilots = fixture_routes.MIGRATED_RESET_PILOT | fixture_routes.ROLLBACK_PILOT
    assert not (fixture_routes.SEED_ASSUMING & pilots)
    assert not (fixture_routes.SEED_ASSUMING & fixture_routes.KEPT_EXISTING)
    for name in fixture_routes.SEED_ASSUMING:
        assert fixture_routes.route_for(name) == "existing"
