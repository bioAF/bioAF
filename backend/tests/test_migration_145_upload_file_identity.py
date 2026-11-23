"""Migration 145: upload checkpoints reserve a unique File UUID; existing rows keep none."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "145_upload_file_identity.py"


def _run(sync_conn, step):
    spec = importlib.util.spec_from_file_location("migration_145", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        getattr(module, step)()


async def _insert(session, upload_id, org_id, extra_cols="", extra_vals=""):
    await session.execute(
        text(
            "INSERT INTO upload_checkpoints (id, organization_id, kind, stage, filename, source_uri, reference"
            f"{extra_cols}) VALUES (:id, :org, 'proxied', 'received', 'a.txt', 'gs://b/uploads/x/a.txt', 'UPL-000000'"
            f"{extra_vals})"
        ).bindparams(id=upload_id, org=org_id)
    )


@pytest.mark.asyncio
async def test_existing_checkpoints_keep_no_reservation_and_reservations_are_unique(session, admin_user):
    org = admin_user.organization_id
    await session.commit()
    conn = await session.connection()
    await conn.run_sync(_run, "downgrade")
    await _insert(session, "legacy", org)
    conn = await session.connection()
    await conn.run_sync(_run, "upgrade")
    row = (
        await session.execute(
            text(
                "SELECT file_uuid, source_version, source_observed_size, source_native_md5, destination_version "
                "FROM upload_checkpoints WHERE id = 'legacy'"
            )
        )
    ).one()
    assert tuple(row) == (None, None, None, None, None)
    uid = "'0f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a'"
    await _insert(session, "first", org, ", file_uuid", f", {uid}")
    with pytest.raises(Exception, match="uq_upload_checkpoints_file_uuid"):
        await _insert(session, "second", org, ", file_uuid", f", {uid}")
    await session.rollback()
