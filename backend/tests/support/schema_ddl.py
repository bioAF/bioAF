"""Build and drop a test schema a batch of tables per transaction.

Postgres holds every lock a transaction takes until it ends, in one shared table of
``max_locks_per_transaction * max_connections`` slots (6400 with the postgres:16 defaults that CI
and local runs use). Dropping a whole test schema in one statement holds about 3200 locks and one
``create_all`` about 1500, so two xdist workers doing either at once exhausted the table ("out of
shared memory") and failed whichever tests were running. Batches keep each transaction under
``LOCK_BUDGET``, so four workers together stay well inside the pool, with room for a worker's
one-time migration build.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.ddl import sort_tables_and_constraints

from tests.plugins import invocation

# The most locks any one build or drop transaction may hold.
LOCK_BUDGET = 640
CREATE_BATCH = 20
DROP_BATCH = 15


def create_batches(metadata: MetaData, size: int = CREATE_BATCH) -> list[list[Table]]:
    """Tables in dependency order, cut into batches; tables in a foreign-key cycle share a batch.

    A batch may reference tables of earlier batches (they already exist). A cycle cannot be split:
    ``create_all`` adds a cycle's constraints after creating both of its tables, so both must be in
    the same call.
    """
    ordered = [t for t, _ in sort_tables_and_constraints(metadata.sorted_tables) if t is not None]
    cyclic: list[set[Table]] = []
    for table, fkcs in sort_tables_and_constraints(metadata.sorted_tables):
        if table is None:
            for fkc in fkcs:
                group = {fkc.table, fkc.referred_table}
                for existing in [g for g in cyclic if g & group]:
                    cyclic.remove(existing)
                    group |= existing
                cyclic.append(group)
    batches: list[list[Table]] = []
    current: list[Table] = []
    for table in ordered:
        current.append(table)
        open_cycle = any(g & set(current) and not g <= set(current) for g in cyclic)
        if len(current) >= size and not open_cycle:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


async def create_schema(
    engine: AsyncEngine,
    schema: str,
    metadata: MetaData,
    on_batch: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """Create ``schema`` and every table of ``metadata`` in it (the engine's search_path names it)."""
    quoted = invocation.quote(schema)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"CREATE SCHEMA {quoted}")
    for batch in create_batches(metadata):
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync, tables=batch: metadata.create_all(sync, tables=tables))
            if on_batch is not None:
                await on_batch(conn)


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def drop_schema(conn, schema: str, on_batch: Callable[..., Awaitable[None]] | None = None) -> None:
    """Drop ``schema`` (if it exists) on an asyncpg connection, a batch of tables per transaction."""
    quoted = invocation.quote(schema)
    rows = await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p') ORDER BY c.relname",
        schema,
    )
    names = [r["relname"] for r in rows]
    for start in range(0, len(names), DROP_BATCH):
        batch = ", ".join(f"{quoted}.{_ident(n)}" for n in names[start : start + DROP_BATCH])
        async with conn.transaction():
            # CASCADE drops only constraints and views that depend on these tables, never other tables.
            await conn.execute(f"DROP TABLE IF EXISTS {batch} CASCADE")
            if on_batch is not None:
                await on_batch(conn)
    async with conn.transaction():
        await conn.execute(f"DROP SCHEMA IF EXISTS {quoted} CASCADE")
        if on_batch is not None:
            await on_batch(conn)
