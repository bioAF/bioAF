"""Migration 128: existing organizations get OpenAlex enabled, once."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "128_literature_source_openalex.py"


def _upgrade(sync_conn):
    spec = importlib.util.spec_from_file_location("migration_128", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


@pytest.mark.asyncio
async def test_existing_org_gets_openalex_enabled_once(session, admin_user):
    org_id = admin_user.organization_id
    conn = await session.connection()
    await conn.run_sync(_upgrade)
    await conn.run_sync(_upgrade)

    rows = (
        await session.execute(
            text(
                "SELECT enabled FROM literature_sources_config WHERE organization_id = :org AND source = 'openalex'"
            ).bindparams(org=org_id)
        )
    ).all()
    assert [r.enabled for r in rows] == [True]
