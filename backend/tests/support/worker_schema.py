"""A migration-built schema per invocation and worker, reused across tests (plan 4, stage 5).

``build`` runs ``alembic upgrade head`` into ``<prefix>_<invocation>_<worker>``
on an explicitly injected connection (logging configuration left alone), then
records the empty-upgrade baseline: every table, the rows migrations seeded
(copied into a sibling ``<prefix>seed_...`` schema), and the seeded tables'
sequence positions. Both schemas are recorded in the invocation manifest and
dropped when the worker's session ends.

``reset`` puts a used schema back to that baseline: delete the rows tests left,
re-insert the seed rows, restore sequences. If a test changed the structure
(``fingerprint``: relations, columns, defaults, indexes, constraints, triggers,
functions, enum labels), the schema is rebuilt instead.

``rollback_scope`` opens one connection and one outer transaction; sessions
from the factory it yields join that transaction through savepoints, and the
whole thing is rolled back on exit. Sequences are not transactional, so it then
puts back any sequence the rolled-back work advanced.

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
    structure: str = ""  # fingerprint() of the empty upgrade


def _q(name: str) -> str:
    return invocation.quote(name)


# Everything a test could change about the structure of one schema, as one digest. Names are
# printed with the schema on the search path, so set it locally for the duration.
_FINGERPRINT_SQL = """
WITH ns AS (SELECT oid FROM pg_namespace WHERE nspname = :s),
parts AS (
    SELECT 'rel ' || c.relname || ' ' || c.relkind::text AS part
    FROM pg_class c WHERE c.relnamespace = (SELECT oid FROM ns)
  UNION ALL
    SELECT 'col ' || c.relname || '.' || a.attname || ' ' || format_type(a.atttypid, a.atttypmod)
        || ' ' || a.attnotnull::text || ' ' || a.attidentity::text || a.attgenerated::text
        || ' ' || coalesce(pg_get_expr(d.adbin, d.adrelid), '')
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid AND c.relnamespace = (SELECT oid FROM ns)
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attnum > 0 AND NOT a.attisdropped
  UNION ALL
    SELECT 'con ' || c.relname || '.' || k.conname || ' ' || pg_get_constraintdef(k.oid)
    FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
    WHERE k.connamespace = (SELECT oid FROM ns)
  UNION ALL
    SELECT 'idx ' || pg_get_indexdef(i.indexrelid)
    FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid AND c.relnamespace = (SELECT oid FROM ns)
  UNION ALL
    SELECT 'trg ' || pg_get_triggerdef(t.oid)
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid AND c.relnamespace = (SELECT oid FROM ns)
    WHERE NOT t.tgisinternal
  UNION ALL
    SELECT 'fn ' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ') ' || md5(p.prosrc)
    FROM pg_proc p WHERE p.pronamespace = (SELECT oid FROM ns)
  UNION ALL
    SELECT 'type ' || t.typname || ' ' || t.typtype::text
        || ' ' || coalesce((SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                            FROM pg_enum e WHERE e.enumtypid = t.oid), '')
    FROM pg_type t WHERE t.typnamespace = (SELECT oid FROM ns) AND t.typtype IN ('e', 'd', 'c')
        AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.oid = t.typrelid AND c.relkind <> 'c')
  UNION ALL
    SELECT 'view ' || c.relname || ' ' || pg_get_viewdef(c.oid)
    FROM pg_class c WHERE c.relnamespace = (SELECT oid FROM ns) AND c.relkind IN ('v', 'm')
)
SELECT md5(coalesce(string_agg(part, E'\\n' ORDER BY part), '')) FROM parts
"""


_PARTS_SQL = _FINGERPRINT_SQL.replace(
    "SELECT md5(coalesce(string_agg(part, E'\\n' ORDER BY part), '')) FROM parts",
    "SELECT part FROM parts ORDER BY part",
)


async def structure(conn, schema: str) -> list[str]:
    """The lines ``fingerprint`` digests, for reporting what differs between two schemas."""
    await conn.execute(text("SELECT set_config('search_path', :s, true)"), {"s": schema})
    return list((await conn.execute(text(_PARTS_SQL), {"s": schema})).scalars())


async def fingerprint(conn, schema: str) -> str:
    """A digest of ``schema``'s structure; equal digests mean no test changed it."""
    await conn.execute(text("SELECT set_config('search_path', :s, true)"), {"s": schema})
    return str((await conn.execute(text(_FINGERPRINT_SQL), {"s": schema})).scalar_one())


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
            structure = await fingerprint(conn, schema)
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
        structure=structure,
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


def _targeted_sql(ws: WorkerSchema, dirty: set[str], moved: set[str]) -> str:
    """Delete only tables holding rows, restart only sequences that moved, restore the seeds."""
    schema = _q(ws.schema)
    clear = dirty | set(ws.seeded)
    statements = [f'DELETE FROM {schema}."{t}"' for t in ws.delete_order if t in clear]
    for table in ws.seeded:
        statements.append(f'INSERT INTO {schema}."{table}" SELECT * FROM {_q(ws.seed_schema)}."{table}"')
    statements.extend(_sequence_sql(ws, moved, all_seeded=True))
    return ";\n".join(statements)


def _sequence_sql(ws: WorkerSchema, moved: set[str], all_seeded: bool) -> list[str]:
    """Put sequences back to their empty-upgrade positions: seeded tables' always (``all_seeded``) or if moved."""
    statements = []
    for seq, (table, baseline) in ws.sequences.items():
        if table in ws.seeded and baseline is not None:
            if all_seeded or seq in moved:
                statements.append(f"SELECT setval('{ws.schema}.\"{seq}\"', {baseline}, true)")
        elif seq in moved:
            statements.append(f"SELECT setval('{ws.schema}.\"{seq}\"', 1, false)")
    return statements


async def _moved(conn, ws: WorkerSchema) -> set[str]:
    """Sequences no longer where the empty upgrade left them (a never-used one has a NULL last_value)."""
    rows = await conn.execute(
        text("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = :s"), {"s": ws.schema}
    )
    moved = set()
    for name, value in rows:
        baseline = ws.sequences.get(str(name), ("", None))[1]
        if value is not None and (baseline is None or int(str(value)) != baseline):
            moved.add(str(name))
    return moved


async def reset(engine: AsyncEngine, ws: WorkerSchema) -> WorkerSchema:
    """Return ``ws`` at its empty-upgrade baseline; rebuild (and return the new one) if its tables changed.

    The targeted reset deletes only tables that hold rows and restarts only sequences that moved,
    which is far cheaper than truncating ~150 tables. If it fails (a foreign-key cycle, say), the
    full TRUNCATE ... RESTART IDENTITY CASCADE reset runs instead.
    """
    started = time.perf_counter()
    async with engine.connect() as conn:
        if await fingerprint(conn, ws.schema) != ws.structure:
            await conn.commit()
            rebuilt = await asyncio.to_thread(build, ws.prefix, ws.worker)
            TIMINGS["rebuild"].append(time.perf_counter() - started)
            return rebuilt
        dirty = set((await conn.execute(text(ws.probe_sql))).scalars()) if ws.probe_sql else set()
        moved = await _moved(conn, ws)
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
async def rollback_scope(engine: AsyncEngine, ws: WorkerSchema) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on one connection inside one outer transaction, rolled back on exit.

    The rollback cannot return sequence values the test consumed, so those are set back afterwards.
    """
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield async_sessionmaker(
                bind=conn, class_=AsyncSession, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            if outer.is_active:
                await outer.rollback()
            async with conn.begin():
                for statement in _sequence_sql(ws, await _moved(conn, ws), all_seeded=False):
                    await conn.execute(text(statement))
