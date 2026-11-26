"""Test schemas are built and dropped within a bounded number of locks per transaction.

Postgres keeps one shared lock table of ``max_locks_per_transaction * max_connections`` slots
(6400 with the postgres:16 defaults CI and local runs use). A single ``DROP SCHEMA ... CASCADE`` of
a full test schema holds about 3200 of them and one ``create_all`` about 1500, so two xdist workers
tearing down at the same moment ran the server "out of shared memory" and failed tests at random.
Building and dropping in batches keeps every transaction under ``LOCK_BUDGET``.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from tests.plugins import invocation
from tests.plugins.db_url import database_url
from tests.support import schema_ddl

PLAIN_URL = database_url().replace("postgresql+asyncpg://", "postgresql://")
LOCKS_HELD = "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()"


def _metadata():
    import app.models  # noqa: F401 -- register all models
    from app.database import Base

    return Base.metadata


async def _tables_in(conn: asyncpg.Connection, schema: str) -> set[str]:
    rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = $1", schema)
    return {r["tablename"] for r in rows}


async def _schema_exists(conn: asyncpg.Connection, schema: str) -> bool:
    return bool(await conn.fetchval("SELECT count(*) FROM pg_namespace WHERE nspname = $1", schema))


async def _build(schema: str, held: list[int] | None = None) -> None:
    engine = create_async_engine(database_url(), connect_args={"server_settings": {"search_path": schema}})
    try:

        async def record(conn) -> None:
            if held is not None:
                held.append((await conn.exec_driver_sql(LOCKS_HELD)).scalar_one())

        await schema_ddl.create_schema(engine, schema, _metadata(), on_batch=record)
    finally:
        await engine.dispose()


def test_batches_cover_every_table_once_and_keep_the_foreign_key_cycle_together():
    metadata = _metadata()
    batches = schema_ddl.create_batches(metadata)
    flat = [t.name for batch in batches for t in batch]
    assert sorted(flat) == sorted(metadata.tables)
    assert len(flat) == len(set(flat))
    together = [b for b in batches if {t.name for t in b} >= {"experiment_templates", "naming_profiles"}]
    assert len(together) == 1


@pytest.mark.asyncio
async def test_building_a_test_schema_stays_within_the_lock_budget(worker_id):
    schema = invocation.claim_schema("lock_budget_build", worker_id)
    held: list[int] = []
    conn = await asyncpg.connect(PLAIN_URL)
    try:
        await _build(schema, held)
        assert await _tables_in(conn, schema) == set(_metadata().tables)
        assert held and max(held) <= schema_ddl.LOCK_BUDGET, held
    finally:
        await schema_ddl.drop_schema(conn, schema)
        await conn.close()


@pytest.mark.asyncio
async def test_dropping_a_test_schema_stays_within_the_lock_budget(worker_id):
    schema = invocation.claim_schema("lock_budget_drop", worker_id)
    await _build(schema)
    conn = await asyncpg.connect(PLAIN_URL)
    try:
        held: list[int] = []

        async def record(c: asyncpg.Connection) -> None:
            held.append(await c.fetchval(LOCKS_HELD))

        await schema_ddl.drop_schema(conn, schema, on_batch=record)
        assert not await _schema_exists(conn, schema)
        assert held and max(held) <= schema_ddl.LOCK_BUDGET, held
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_dropping_a_schema_that_does_not_exist_is_a_no_op(worker_id):
    conn = await asyncpg.connect(PLAIN_URL)
    try:
        await schema_ddl.drop_schema(conn, invocation.claim_schema("lock_budget_absent", worker_id))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_four_workers_dropping_at_once_do_not_exhaust_the_lock_table(worker_id):
    schemas = [invocation.claim_schema(f"lock_budget_par_{c}", worker_id) for c in "abcd"]
    await asyncio.gather(*(_build(s) for s in schemas))
    conns = [await asyncpg.connect(PLAIN_URL) for _ in schemas]
    try:
        results = await asyncio.gather(
            *(schema_ddl.drop_schema(c, s) for c, s in zip(conns, schemas)), return_exceptions=True
        )
        assert [r for r in results if isinstance(r, BaseException)] == []
        for s in schemas:
            assert not await _schema_exists(conns[0], s)
    finally:
        for s in schemas:
            await conns[0].execute(f"DROP SCHEMA IF EXISTS {invocation.quote(s)} CASCADE")
        for c in conns:
            await c.close()


def test_the_invocation_drop_uses_the_batched_drop(monkeypatch):
    dropped: list[str] = []

    async def fake_drop(conn, name, on_batch=None):
        dropped.append(name)

    monkeypatch.setattr(schema_ddl, "drop_schema", fake_drop)
    invocation.drop_schemas(["test_ab12cd_gw0", "pilot_ab12cd_gw0"])
    assert dropped == ["test_ab12cd_gw0", "pilot_ab12cd_gw0"]
