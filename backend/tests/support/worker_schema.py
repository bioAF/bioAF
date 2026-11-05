"""A migration-built schema per invocation and worker, reused across tests (plan 4, stage 5).

``build`` runs ``alembic upgrade head`` into ``<prefix>_<invocation>_<worker>``
on an explicitly injected connection (logging configuration left alone), then
records the empty-upgrade baseline: every table, the rows migrations seeded
(copied into a sibling ``<prefix>seed_...`` schema), and the seeded tables'
sequence positions. Both schemas are recorded in the invocation manifest and
dropped when the worker's session ends.

``reset`` puts a used schema back to that baseline in one round trip:
TRUNCATE every table with RESTART IDENTITY, re-insert the seed rows, restore
their sequences. If a test changed the set of tables, the schema is rebuilt.

``rollback_scope`` opens one connection and one outer transaction; sessions
from the factory it yields join that transaction through savepoints, and the
whole thing is rolled back on exit.

Engines stay function-scoped: only the DDL is reused, so pooled connections
never cross the per-test event loops.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from tests.plugins import invocation
from tests.plugins.db_url import database_url

ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"

# Seconds spent per operation, for the runtime comparison (read by tests/support/fixture_routes).
TIMINGS: dict[str, list[float]] = {"build": [], "reset": [], "rebuild": [], "fallback": []}


@dataclass
class WorkerSchema:
    prefix: str
    worker: str
    schema: str
    seed_schema: str
    tables: frozenset[str]
    reset_sql: str  # the full TRUNCATE reset, used when the targeted one fails
    seeded: dict[str, int] = field(default_factory=dict)
    # Children before parents, so deleting in this order never violates a foreign key.
    delete_order: tuple[str, ...] = ()
    # sequence -> (owning table, value after the empty upgrade or None if never used)
    sequences: dict[str, tuple[str, int | None]] = field(default_factory=dict)
    probe_sql: str = ""


def _q(name: str) -> str:
    return invocation.quote(name)


async def _build(prefix: str, worker: str) -> WorkerSchema:
    from alembic import command
    from alembic.config import Config

    schema = invocation.claim_schema(prefix, worker)
    seed_schema = invocation.claim_schema(f"{prefix}seed", worker)
    url = database_url()
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as conn:
            for name in (schema, seed_schema):
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {_q(name)} CASCADE"))
                await conn.execute(text(f"CREATE SCHEMA {_q(name)}"))

        cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.set_main_option("sqlalchemy.url", url)
        async with engine.connect() as conn:

            def _upgrade(sync_conn):
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_logging_config"] = True
                command.upgrade(cfg, "head")

            await conn.run_sync(_upgrade)
            await conn.commit()

        async with admin.begin() as conn:
            tables = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = :s AND tablename <> 'alembic_version'"
                        ),
                        {"s": schema},
                    )
                )
                .scalars()
                .all()
            )
            seeded: dict[str, int] = {}
            for table in sorted(tables):
                count = (await conn.execute(text(f'SELECT count(*) FROM {_q(schema)}."{table}"'))).scalar_one()
                if count:
                    seeded[table] = count
                    await conn.execute(
                        text(f'CREATE TABLE {_q(seed_schema)}."{table}" AS TABLE {_q(schema)}."{table}"')
                    )
            # Sequences of seeded tables continue after their seed rows; the rest restart at 1.
            sequences = (
                await conn.execute(
                    text(
                        """
                        SELECT s.relname, t.relname, sq.last_value
                        FROM pg_class s
                        JOIN pg_namespace n ON n.oid = s.relnamespace
                        JOIN pg_depend d ON d.objid = s.oid AND d.deptype IN ('a', 'i')
                        JOIN pg_class t ON t.oid = d.refobjid
                        JOIN pg_sequences sq ON sq.schemaname = n.nspname AND sq.sequencename = s.relname
                        WHERE s.relkind = 'S' AND n.nspname = :s
                        """
                    ),
                    {"s": schema},
                )
            ).all()
            foreign_keys = (
                await conn.execute(
                    text(
                        """
                        SELECT child.relname, parent.relname
                        FROM pg_constraint c
                        JOIN pg_class child ON child.oid = c.conrelid
                        JOIN pg_class parent ON parent.oid = c.confrelid
                        JOIN pg_namespace n ON n.oid = child.relnamespace
                        WHERE c.contype = 'f' AND n.nspname = :s AND child.oid <> parent.oid
                        """
                    ),
                    {"s": schema},
                )
            ).all()
    finally:
        await engine.dispose()
        await admin.dispose()

    statements = []
    if tables:
        table_list = ", ".join(f'{_q(schema)}."{t}"' for t in sorted(tables))
        statements.append(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")
    for table in seeded:
        statements.append(f'INSERT INTO {_q(schema)}."{table}" SELECT * FROM {_q(seed_schema)}."{table}"')
    # pg_sequences.last_value is NULL for a sequence never used; RESTART IDENTITY already covers those.
    for seq, table, last_value in sequences:
        if table in seeded and last_value is not None:
            statements.append(f"SELECT setval('{schema}.\"{seq}\"', {int(str(last_value))}, true)")
    probe = " UNION ALL ".join(
        f"SELECT '{t}'::text WHERE EXISTS (SELECT 1 FROM {_q(schema)}.\"{t}\")" for t in sorted(tables)
    )
    return WorkerSchema(
        prefix=prefix,
        worker=worker,
        schema=schema,
        seed_schema=seed_schema,
        tables=frozenset(tables),
        reset_sql=";\n".join(statements),
        seeded=seeded,
        delete_order=_children_first(tables, foreign_keys),
        sequences={str(seq): (str(table), None if v is None else int(str(v))) for seq, table, v in sequences},
        probe_sql=probe,
    )


def _children_first(tables, foreign_keys) -> tuple[str, ...]:
    """Tables ordered so every child precedes its parents; a cycle is broken arbitrarily.

    A broken cycle can make a targeted delete fail on a foreign key; ``reset`` then falls back to
    truncating everything.
    """
    parents: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in foreign_keys:
        if child in parents and parent in parents:
            parents[child].add(parent)
    order: list[str] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(t: str) -> None:  # depth-first: parents land in `order` before children
        if t in done or t in visiting:
            return
        visiting.add(t)
        for p in sorted(parents[t]):
            visit(p)
        visiting.discard(t)
        done.add(t)
        order.append(t)

    for t in sorted(tables):
        visit(t)
    return tuple(reversed(order))


def build(prefix: str, worker: str) -> WorkerSchema:
    """Build synchronously, on a private event loop (call from a thread inside async code)."""
    started = time.perf_counter()
    ws = asyncio.run(_build(prefix, worker))
    TIMINGS["build"].append(time.perf_counter() - started)
    return ws


def drop(ws: WorkerSchema) -> None:
    invocation.drop_schemas([ws.schema, ws.seed_schema])


async def _current_tables(conn, schema: str) -> frozenset[str]:
    rows = await conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = :s AND tablename <> 'alembic_version'"),
        {"s": schema},
    )
    return frozenset(rows.scalars())


def _targeted_sql(ws: WorkerSchema, dirty: set[str], moved: set[str]) -> str:
    """Delete only tables holding rows, restart only sequences that moved, restore the seeds."""
    schema = _q(ws.schema)
    clear = dirty | set(ws.seeded)
    statements = [f'DELETE FROM {schema}."{t}"' for t in ws.delete_order if t in clear]
    for table in ws.seeded:
        statements.append(f'INSERT INTO {schema}."{table}" SELECT * FROM {_q(ws.seed_schema)}."{table}"')
    for seq, (table, baseline) in ws.sequences.items():
        if table in ws.seeded and baseline is not None:
            statements.append(f"SELECT setval('{ws.schema}.\"{seq}\"', {baseline}, true)")
        elif seq in moved:
            statements.append(f"SELECT setval('{ws.schema}.\"{seq}\"', 1, false)")
    return ";\n".join(statements)


async def reset(engine: AsyncEngine, ws: WorkerSchema) -> WorkerSchema:
    """Return ``ws`` at its empty-upgrade baseline; rebuild (and return the new one) if its tables changed.

    The targeted reset deletes only tables that hold rows and restarts only sequences that moved,
    which is far cheaper than truncating ~150 tables. If it fails (a foreign-key cycle, say), the
    full TRUNCATE ... RESTART IDENTITY CASCADE reset runs instead.
    """
    started = time.perf_counter()
    async with engine.connect() as conn:
        if await _current_tables(conn, ws.schema) != ws.tables:
            rebuilt = await asyncio.to_thread(build, ws.prefix, ws.worker)
            TIMINGS["rebuild"].append(time.perf_counter() - started)
            return rebuilt
        dirty = set((await conn.execute(text(ws.probe_sql))).scalars()) if ws.probe_sql else set()
        moved = set(
            (
                await conn.execute(
                    text("SELECT sequencename FROM pg_sequences WHERE schemaname = :s AND last_value IS NOT NULL"),
                    {"s": ws.schema},
                )
            ).scalars()
        )
        await conn.commit()
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        try:
            # One simple-protocol round trip; its statements run as one implicit transaction.
            await driver.execute(_targeted_sql(ws, dirty, moved))  # type: ignore[union-attr]
        except Exception:
            TIMINGS["fallback"].append(time.perf_counter() - started)
            await driver.execute(ws.reset_sql)  # type: ignore[union-attr]
    TIMINGS["reset"].append(time.perf_counter() - started)
    return ws


@contextlib.asynccontextmanager
async def rollback_scope(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on one connection inside one outer transaction, rolled back on exit."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield async_sessionmaker(
                bind=conn, class_=AsyncSession, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            if outer.is_active:
                await outer.rollback()
