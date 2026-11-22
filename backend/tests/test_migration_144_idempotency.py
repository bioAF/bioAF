"""Migration 144: one operation per principal and Idempotency-Key, keeping the newest."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "144_unique_operation_idempotency_key.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_144", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(sync_conn, step):
    with Operations.context(MigrationContext.configure(sync_conn)):
        getattr(_module(), step)()


@pytest.mark.asyncio
async def test_duplicate_keys_keep_the_newest_operation_and_the_key_becomes_unique(session):
    conn = await session.connection()
    await conn.run_sync(_run, "downgrade")
    for status in ("failed", "succeeded"):
        await session.execute(
            text(
                "INSERT INTO infrastructure_operations (action, status, principal, idempotency_key, steps) "
                "VALUES ('compute_deploy', :s, 'user:1', 'dup', '[]')"
            ).bindparams(s=status)
        )
    conn = await session.connection()
    await conn.run_sync(_run, "upgrade")
    rows = (
        await session.execute(text("SELECT status, idempotency_key FROM infrastructure_operations ORDER BY id"))
    ).all()
    assert [tuple(r) for r in rows] == [("failed", None), ("succeeded", "dup")]
    with pytest.raises(Exception, match="uq_infrastructure_operations_idempotency"):
        await session.execute(
            text(
                "INSERT INTO infrastructure_operations (action, status, principal, idempotency_key, steps) "
                "VALUES ('compute_deploy', 'failed', 'user:1', 'dup', '[]')"
            )
        )
    await session.rollback()
