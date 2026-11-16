"""Migrations 134 and 135: file identity, source identity, readiness, and verification records.

134 adds the nullable file external ID and the durable pipeline-output source identity. It backfills a
source identity only where registration evidence is unambiguous, leaves every member of a duplicate
group (and any row moved after registration) NULL, and reports their File IDs without choosing a
winner. 135 adds the readiness marker and the persisted verification record. Neither fabricates an
external ID, a ready timestamp, or a checksum proof.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.plugins import invocation

from .conftest import TEST_DATABASE_URL

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def stepped(worker_id):
    schema = invocation.claim_schema("fileid_migration", worker_id)
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {invocation.quote(schema)}"))

    cfg = Config(str(ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    async def run(fn, revision: str) -> None:
        async with engine.connect() as conn:

            def _run(sync_conn):
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_logging_config"] = True
                fn(cfg, revision)

            await conn.run_sync(_run)
            await conn.commit()

    yield engine, run

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {invocation.quote(schema)} CASCADE"))
    await engine.dispose()


async def _seed(engine) -> dict[str, int]:
    """Legacy rows at revision 133. Returns file IDs by role."""
    ids: dict[str, int] = {}
    async with engine.begin() as conn:
        org = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('Lab') RETURNING id"))).scalar_one()
        org2 = (await conn.execute(text("INSERT INTO organizations (name) VALUES ('Other') RETURNING id"))).scalar_one()
        run = (
            await conn.execute(
                text(
                    "INSERT INTO pipeline_runs (organization_id, pipeline_name, status) "
                    "VALUES (:o, 'rnaseq', 'completed') RETURNING id"
                ),
                {"o": org},
            )
        ).scalar_one()
        run2 = (
            await conn.execute(
                text(
                    "INSERT INTO pipeline_runs (organization_id, pipeline_name, status) "
                    "VALUES (:o, 'rnaseq', 'completed') RETURNING id"
                ),
                {"o": org2},
            )
        ).scalar_one()

        async def add(role, uri, *, org_id=org, run_id=run, source="pipeline_output", md5=None, deleted=False):
            ids[role] = (
                await conn.execute(
                    text(
                        "INSERT INTO files (organization_id, gcs_uri, storage_uri, filename, file_type, "
                        "source_type, source_pipeline_run_id, md5_checksum, deleted_at) "
                        "VALUES (:o, :u, :u, 'f.txt', 'other', :s, :r, :m, "
                        "CASE WHEN :d THEN now() ELSE NULL END) RETURNING id"
                    ),
                    {"o": org_id, "u": uri, "s": source, "r": run_id, "m": md5, "d": deleted},
                )
            ).scalar_one()

        await add("unique", "s3://raw/run/a.bam", md5="0" * 32)
        await add("dup1", "s3://raw/run/b.bam")
        await add("dup2", "s3://raw/run/b.bam")
        await add("moved", "s3://raw/exp/c.bam")
        await add("tombstoned", "s3://raw/run/d.bam", deleted=True)
        await add("upload", "s3://raw/up/e.fastq", source="upload", run_id=None)
        # The same URI string under another organization's run is a different scoped identity.
        await add("other_org", "s3://raw/run/a.bam", org_id=org2, run_id=run2)
        await conn.execute(
            text(
                "INSERT INTO audit_log (entity_type, entity_id, action) VALUES ('file', :f, 'reassigned_to_experiment')"
            ),
            {"f": ids["moved"]},
        )
        ids["org"], ids["org2"], ids["run"] = org, org2, run
    return ids


async def _files(engine) -> dict[int, dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT * FROM files"))).mappings().all()
    return {r["id"]: dict(r) for r in rows}


async def test_134_backfills_only_unambiguous_sources_and_reports_conflicts(stepped, caplog):
    engine, run = stepped
    await run(command.upgrade, "133")
    ids = await _seed(engine)
    before = await _files(engine)

    with caplog.at_level(logging.WARNING):
        await run(command.upgrade, "134")

    after = await _files(engine)
    assert {fid: row["uuid"] for fid, row in after.items()} == {fid: row["uuid"] for fid, row in before.items()}
    assert all(row["external_id"] is None for row in after.values())
    assert after[ids["unique"]]["source_storage_uri"] == "s3://raw/run/a.bam"
    assert after[ids["tombstoned"]]["source_storage_uri"] == "s3://raw/run/d.bam"
    assert after[ids["other_org"]]["source_storage_uri"] == "s3://raw/run/a.bam"
    assert after[ids["dup1"]]["source_storage_uri"] is None
    assert after[ids["dup2"]]["source_storage_uri"] is None
    assert after[ids["moved"]]["source_storage_uri"] is None
    assert after[ids["upload"]]["source_storage_uri"] is None
    # Legacy checksum values are kept, not certified or erased.
    assert after[ids["unique"]]["md5_checksum"] == "0" * 32

    report = " ".join(r.getMessage() for r in caplog.records)
    assert str(ids["dup1"]) in report and str(ids["dup2"]) in report and str(ids["moved"]) in report


async def test_134_enforces_scoped_uniqueness(stepped):
    engine, run = stepped
    await run(command.upgrade, "133")
    ids = await _seed(engine)
    await run(command.upgrade, "134")

    async with engine.begin() as conn:
        await conn.execute(text("UPDATE files SET external_id = '007' WHERE id = :f"), {"f": ids["unique"]})
        # Another organization may reuse the value; nulls never collide.
        await conn.execute(text("UPDATE files SET external_id = '007' WHERE id = :f"), {"f": ids["other_org"]})
    for target in ("tombstoned", "dup1"):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE files SET external_id = '007' WHERE id = :f"), {"f": ids[target]})

    # A second registration of the same scoped source identity is refused, including a tombstone's.
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE files SET source_storage_uri = 's3://raw/run/d.bam' WHERE id = :f"), {"f": ids["dup1"]}
            )


async def test_135_adds_readiness_and_verification_without_fabricating_state(stepped):
    engine, run = stepped
    await run(command.upgrade, "133")
    ids = await _seed(engine)
    await run(command.upgrade, "135")

    after = await _files(engine)
    assert all(row["ready_at"] is None for row in after.values())
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM file_verifications"))).scalar_one() == 0
        await conn.execute(
            text(
                "INSERT INTO file_verifications (organization_id, file_id, status) VALUES (:o, :f, 'not_required')"
            ),
            {"o": ids["org"], "f": ids["unique"]},
        )
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("INSERT INTO file_verifications (organization_id, file_id, status) VALUES (:o, :f, 'queued')"),
                {"o": ids["org"], "f": ids["unique"]},
            )


async def test_downgrade_removes_both_revisions(stepped):
    engine, run = stepped
    await run(command.upgrade, "133")
    await _seed(engine)
    await run(command.upgrade, "135")
    await run(command.downgrade, "133")

    async with engine.connect() as conn:
        cols = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'files' AND table_schema = current_schema()"
                    )
                )
            ).all()
        }
        tables = {
            r[0]
            for r in (
                await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
                )
            ).all()
        }
    assert not {"external_id", "source_storage_uri", "ready_at"} & cols
    assert "file_verifications" not in tables
    # And upgrading again works.
    await run(command.upgrade, "135")
