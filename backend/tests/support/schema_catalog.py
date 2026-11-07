"""Read a schema's enforced rules from the PostgreSQL catalogs, independent of object names.

Two schemas built differently (``alembic upgrade head`` versus
``Base.metadata.create_all``) name their indexes and constraints differently, so
they are compared by what they enforce:

- ``unique``: every unique index or constraint, as ``(table, "btree (cols) WHERE pred")``
- ``foreign_keys``: ``(table, "FOREIGN KEY (col) REFERENCES other(id) ON DELETE ...")``
- ``checks``: ``(table, "CHECK (...)")``
- ``not_null``: ``(table, column)`` for every NOT NULL column
- ``seeded``: tables that hold rows, with their row counts
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _strip(schema: str, value: str) -> str:
    return value.replace(f"{schema}.", "").replace(f'"{schema}".', "")


async def read(conn: AsyncConnection, schema: str, *, count_rows: bool = True) -> dict[str, Any]:
    params = {"s": schema}
    unique = await conn.execute(
        text(
            """
            SELECT t.relname, regexp_replace(pg_get_indexdef(i.indexrelid), '^.* USING ', '')
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = :s AND i.indisunique AND NOT i.indisprimary
            """
        ),
        params,
    )
    constraints = await conn.execute(
        text(
            """
            SELECT t.relname, c.contype, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = :s AND c.contype IN ('f', 'c')
            """
        ),
        params,
    )
    not_null = await conn.execute(
        text(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = :s AND is_nullable = 'NO'
            """
        ),
        params,
    )
    tables = (
        (
            await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :s AND tablename <> 'alembic_version'"),
                params,
            )
        )
        .scalars()
        .all()
    )
    seeded: dict[str, int] = {}
    if count_rows:
        for table in tables:
            count = (await conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"'))).scalar_one()
            if count:
                seeded[table] = count
    rows = constraints.all()
    return {
        "unique": {(t, _strip(schema, d)) for t, d in unique.all()},
        "foreign_keys": {(t, _strip(schema, d)) for t, kind, d in rows if kind == "f"},
        "checks": {(t, _strip(schema, d)) for t, kind, d in rows if kind == "c"},
        "not_null": {(t, c) for t, c in not_null.all()},
        "tables": set(tables),
        "seeded": seeded,
    }


async def named_unique(conn: AsyncConnection, schema: str) -> dict[tuple[str, str], tuple[str, str]]:
    """Every unique rule by ``(table, name)``: ``("constraint" | "index", "btree (cols) WHERE pred")``.

    ``read`` ignores names on purpose; this is for checking that a model declares a migration's
    rule under the same name and as the same kind of object, which autogenerate compares by name.
    """
    rows = await conn.execute(
        text(
            """
            SELECT t.relname, ic.relname,
                   CASE WHEN con.oid IS NULL THEN 'index' ELSE 'constraint' END,
                   regexp_replace(pg_get_indexdef(i.indexrelid), '^.* USING ', '')
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_class ic ON ic.oid = i.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            LEFT JOIN pg_constraint con ON con.conindid = i.indexrelid AND con.contype = 'u'
            WHERE n.nspname = :s AND i.indisunique AND NOT i.indisprimary
            """
        ),
        {"s": schema},
    )
    return {(t, name): (kind, _strip(schema, d)) for t, name, kind, d in rows.all()}


def differences(migrated: dict[str, Any], created: dict[str, Any]) -> dict[str, dict[str, list]]:
    """Per rule kind, what only the migrated schema enforces and what only create_all enforces.

    Rules on tables that exist in only one of the schemas are left to the table/column parity test.
    """
    shared = migrated["tables"] & created["tables"]
    out: dict[str, dict[str, list]] = {}
    for kind in ("unique", "foreign_keys", "checks", "not_null"):
        m = {r for r in migrated[kind] if r[0] in shared}
        c = {r for r in created[kind] if r[0] in shared}
        out[kind] = {"migrations_only": sorted(m - c), "models_only": sorted(c - m)}
    return out
