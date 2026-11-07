"""The database upgrade lifecycle every entry point shares (plan 9, section 3).

Each test starts from a real released schema with data (the last release before the candidate's head),
runs the candidate exactly as production does (a separate process using this checkout's alembic.ini,
env.py and app.schema_upgrade), and checks what the owner decided an update must do:

- a verified recovery backup exists before anything changes, and a backup that cannot be made or
  verified stops the update with nothing changed and a useful record (owner decision 2026-11-07,
  replacing warn-and-continue);
- every entry point (the entrypoint's ``python -m app.schema_upgrade run`` and an older updater's own
  ``alembic upgrade head``) goes through the same lock, backup and record, and a second runner waits
  and then does nothing;
- a failed migration leaves the database where it was, a useful record, and a working retry;
- the recovery material restores, including the gzipped plain SQL the host updater wrote.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import subprocess

import asyncpg
import pytest

from tests.support import historical_runner as runner
from tests.support import releases
from tests.support.case_db import CaseDatabase, case_database, code_head, pg_bin_dir, plain

ENTRYPOINT_RUN = ("-m", "app.schema_upgrade", "run")


@pytest.fixture
def case_db():
    yield from case_database()


def _previous_release() -> dict:
    """The newest release whose head is behind the candidate's: an install with work to do."""
    head = int(code_head())
    return max((r for r in releases.current() if int(r["head"]) < head), key=lambda r: int(r["head"]))


async def _populate(case: CaseDatabase) -> None:
    conn = await case.connect()
    try:
        org = await conn.fetchval("INSERT INTO organizations (name) VALUES ('Lab') RETURNING id")
        project = await conn.fetchval(
            "INSERT INTO projects (organization_id, name, status) VALUES ($1, 'p', 'active') RETURNING id", org
        )
        exp = await conn.fetchval(
            "INSERT INTO experiments (organization_id, project_id, name, status) VALUES ($1, $2, 'e', 'registered') "
            "RETURNING id",
            org,
            project,
        )
        for i in range(5):
            await conn.execute(
                "INSERT INTO samples (experiment_id, external_id, status) VALUES ($1, $2, 'registered')", exp, f"S-{i}"
            )
        await conn.execute("UPDATE platform_config SET value = 'lab-project' WHERE key = 'gcp_project_id'")
    finally:
        await conn.close()


async def _state(case: CaseDatabase) -> dict:
    conn = await case.connect()
    try:
        tables = [
            r[0] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        ]
        return {
            "revision": await conn.fetchval("SELECT version_num FROM alembic_version"),
            "tables": tables,
            "rows": {t: [tuple(r) for r in await conn.fetch(f'SELECT * FROM "{t}" ORDER BY 1')] for t in tables},
        }
    finally:
        await conn.close()


def _record(case: CaseDatabase) -> dict:
    path = case.status / "schema-upgrade.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _dumps(case: CaseDatabase) -> list:
    return sorted(case.backups.glob("*.dump")) if case.backups.exists() else []


@pytest.fixture
def released(case_db):
    release = _previous_release()
    runner.build(release["commit"], release["head"], case_db.url)
    asyncio.run(_populate(case_db))
    return case_db, release


def test_an_upgrade_backs_up_verifies_migrates_and_records(released):
    case, release = released
    before = asyncio.run(_state(case))

    done = case.run(*ENTRYPOINT_RUN)
    assert done.returncode == 0, done.stderr[-3000:]

    record = _record(case)
    assert record["status"] == "completed" and record["step"] == "validated"
    assert (record["from_revision"], record["target_revision"]) == (release["head"], code_head())
    [dump] = _dumps(case)
    manifest = json.loads(dump.with_suffix(".json").read_text())
    assert record["backup"] == manifest
    assert manifest["revision"] == release["head"] and manifest["tables"] == len(before["tables"])
    assert manifest["server_version"].startswith("16") and "16." in manifest["pg_dump"]
    assert manifest["encryption_key_fingerprints"] and runner.TEST_ENCRYPTION_KEY not in json.dumps(manifest)
    assert not list(case.backups.glob("*.partial"))
    assert asyncio.run(_state(case))["revision"] == code_head()


def test_the_recovery_dump_restores_the_pre_upgrade_database(released, tmp_path):
    case, _ = released
    before = asyncio.run(_state(case))
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    [dump] = _dumps(case)

    target = CaseDatabase()
    asyncio.run(target.create())
    try:
        restored = subprocess.run(
            [
                str(pg_bin_dir() / "pg_restore"),
                "--no-owner",
                "--exit-on-error",
                "--dbname",
                plain(target.url),
                str(dump),
            ],
            capture_output=True,
            text=True,
        )
        assert restored.returncode == 0, restored.stderr[-2000:]
        assert asyncio.run(_state(target)) == before
    finally:
        asyncio.run(target.drop())


def test_a_gzipped_plain_sql_backup_from_the_host_updater_restores(released):
    """The format `./bioaf update` and `./bioaf backup` have always written: pg_dump | gzip."""
    case, _ = released
    before = asyncio.run(_state(case))
    case.backups.mkdir(parents=True, exist_ok=True)
    legacy = case.backups / "bioaf_pre_update_legacy.sql.gz"
    dumped = subprocess.run(
        [str(pg_bin_dir() / "pg_dump"), "--dbname", plain(case.url)], capture_output=True, check=True
    ).stdout
    legacy.write_bytes(gzip.compress(dumped))

    target = CaseDatabase()
    asyncio.run(target.create())
    try:
        restored = subprocess.run(
            [str(pg_bin_dir() / "psql"), "--set", "ON_ERROR_STOP=1", "--quiet", "--dbname", plain(target.url)],
            input=gzip.decompress(legacy.read_bytes()),
            capture_output=True,
        )
        assert restored.returncode == 0, restored.stderr[-2000:]
        assert asyncio.run(_state(target)) == before
    finally:
        asyncio.run(target.drop())


@pytest.mark.parametrize(
    "breakage",
    [
        pytest.param({"BIOAF_UPGRADE_PG_BIN_DIR": "/nonexistent"}, id="no-postgresql-16-client"),
        pytest.param({"BIOAF_UPGRADE_BACKUP_RESERVE_BYTES": str(1 << 62)}, id="not-enough-space"),
        pytest.param({"BIOAF_UPGRADE_BACKUPS_DIR": "/proc/forbidden"}, id="backups-not-writable"),
    ],
)
def test_a_backup_that_cannot_be_made_stops_the_update_with_nothing_changed(released, breakage):
    case, release = released
    before = asyncio.run(_state(case))

    failed = case.run(*ENTRYPOINT_RUN, **breakage)
    assert failed.returncode == 1
    assert "Database upgrade failed" in failed.stderr and "schema-upgrade.json" in failed.stderr

    assert asyncio.run(_state(case)) == before, "a failed backup must not let the upgrade change anything"
    record = _record(case)
    assert (record["status"], record["step"]) == ("failed", "backup")
    assert record["error"].startswith("BackupFailed")
    assert not list(case.backups.glob("*.partial")) if case.backups.exists() else True

    # The cause fixed, the same command finishes the job.
    retried = case.run(*ENTRYPOINT_RUN)
    assert retried.returncode == 0, retried.stderr[-3000:]
    assert _record(case)["status"] == "completed"


def test_a_dump_that_does_not_verify_is_discarded_and_stops_the_update(released, tmp_path):
    """A pg_dump that exits 0 but writes an unreadable archive is not a backup."""
    case, _ = released
    fake = tmp_path / "bin"
    fake.mkdir()
    real = pg_bin_dir()
    (fake / "pg_dump").write_text(
        '#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ "$1" = --file ]; then echo garbage > "$2"; fi; shift; done\n'
    )
    (fake / "pg_dump").chmod(0o755)
    (fake / "pg_restore").symlink_to(real / "pg_restore")
    before = asyncio.run(_state(case))

    failed = case.run(*ENTRYPOINT_RUN, BIOAF_UPGRADE_PG_BIN_DIR=str(fake))
    assert failed.returncode == 1
    assert asyncio.run(_state(case)) == before
    assert "pg_restore --list could not read the dump" in _record(case)["error"]
    assert _dumps(case) == [] and not list(case.backups.glob("*.partial"))


def test_an_older_updaters_own_alembic_call_goes_through_the_same_lifecycle(released):
    """`./bioaf update` before this release ends with `alembic upgrade head` in the new container."""
    case, release = released
    done = case.run()  # the default: alembic -c alembic/alembic.ini upgrade head
    assert done.returncode == 0, done.stderr[-3000:]
    assert _record(case)["status"] == "completed"
    assert len(_dumps(case)) == 1


def test_a_repeated_run_is_a_no_op_that_keeps_the_recovery_set(released):
    case, _ = released
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    first_record, first_dumps = _record(case), _dumps(case)

    for argv in (ENTRYPOINT_RUN, ()):
        again = case.run(*argv)
        assert again.returncode == 0, again.stderr[-3000:]
    assert _dumps(case) == first_dumps
    assert _record(case) == first_record


def test_a_second_runner_waits_for_the_first_then_has_nothing_to_do(released):
    """The entrypoint and an old updater's alembic call start together; one upgrade, one backup."""
    case, _ = released

    async def _start_both_while_the_lock_is_held():
        holder = await case.connect()
        await holder.execute("SELECT pg_advisory_lock($1)", int.from_bytes(b"bioafdb", "big"))
        started = [
            case.popen(*ENTRYPOINT_RUN),
            case.popen("-m", "alembic", "-c", "alembic/alembic.ini", "upgrade", "head"),
        ]
        await asyncio.sleep(3)
        assert all(p.poll() is None for p in started), "a runner did not wait for the lock"
        assert _record(case) == {} and _dumps(case) == []
        await holder.close()
        return started

    waiting = asyncio.run(_start_both_while_the_lock_is_held())
    outcomes = [p.communicate(timeout=300) and p.returncode for p in waiting]
    assert outcomes == [0, 0]
    assert len(_dumps(case)) == 1
    assert _record(case)["status"] == "completed"


def test_a_failed_migration_is_recorded_rolled_back_and_retryable(released):
    case, release = released

    async def _block():
        conn = await case.connect()
        try:
            # Migration 132 creates this table IF NOT EXISTS, then indexes columns this one lacks.
            await conn.execute("CREATE TABLE sample_external_id_transitions (id int)")
        finally:
            await conn.close()

    asyncio.run(_block())
    before = asyncio.run(_state(case))
    failed = case.run(*ENTRYPOINT_RUN)
    assert failed.returncode == 1

    record = _record(case)
    assert (record["status"], record["step"]) == ("failed", "migrate")
    assert record["backup"]["path"] and len(_dumps(case)) == 1, "the recovery set made before the attempt is kept"
    assert asyncio.run(_state(case)) == before

    async def _unblock():
        conn = await case.connect()
        try:
            await conn.execute("DROP TABLE sample_external_id_transitions")
        finally:
            await conn.close()

    asyncio.run(_unblock())
    retried = case.run(*ENTRYPOINT_RUN)
    assert retried.returncode == 0, retried.stderr[-3000:]
    assert _record(case)["status"] == "completed"
    assert len(_dumps(case)) == 2, "the retry makes its own recovery set and keeps the first"


def test_a_fresh_install_has_nothing_to_back_up(case_db):
    done = case_db.run(*ENTRYPOINT_RUN)
    assert done.returncode == 0, done.stderr[-3000:]
    record = _record(case_db)
    assert record["status"] == "completed" and record["from_revision"] is None
    assert "fresh install" in record["backup"]["skipped"]
    assert _dumps(case_db) == []


@pytest.mark.asyncio
async def test_the_gate_reads_the_database_and_the_record(released, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schema_upgrade
    from app.config import settings

    case, release = released
    monkeypatch.setattr(settings, "update_status_dir", str(case.status))
    engine = create_async_engine(case.url)
    try:
        behind = await schema_upgrade.compatibility(engine)
        assert (behind["compatible"], behind["reason"], behind["revision"]) == (
            False,
            "upgrade_required",
            release["head"],
        )

        conn = await asyncpg.connect(plain(case.url))
        await conn.execute("SELECT pg_advisory_lock($1)", schema_upgrade.LOCK_KEY)
        assert (await schema_upgrade.compatibility(engine))["reason"] == "upgrade_in_progress"
        await conn.close()

        failed = await asyncio.to_thread(case.run, *ENTRYPOINT_RUN, BIOAF_UPGRADE_PG_BIN_DIR="/nonexistent")
        assert failed.returncode == 1
        stopped = await schema_upgrade.compatibility(engine)
        assert stopped["reason"] == "upgrade_failed"
        assert stopped["upgrade"]["error"] == "BackupFailed" and "path" not in json.dumps(stopped)

        assert (await asyncio.to_thread(case.run, *ENTRYPOINT_RUN)).returncode == 0
        assert (await schema_upgrade.compatibility(engine)) | {"upgrade": None} == {
            "compatible": True,
            "reason": None,
            "revision": code_head(),
            "target": code_head(),
            "upgrade": None,
        }
    finally:
        await engine.dispose()
