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
import hashlib
import json
import subprocess

import asyncpg
import pytest
from cryptography.fernet import Fernet

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
        # A secret stored as the application stores it: Fernet ciphertext under the install's key.
        await conn.execute(
            "INSERT INTO slack_webhooks (organization_id, name, webhook_url) VALUES ($1, 'hook', $2)",
            org,
            Fernet(runner.TEST_ENCRYPTION_KEY.encode()).encrypt(b"https://hooks.example/lab").decode(),
        )
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


def test_recovery_material_is_private_to_the_backups_owner(released):
    """A dump holds the whole database: 0600 files in a 0700 directory, owned like the directory."""
    import os
    import stat

    case, _ = released
    case.backups.mkdir(parents=True, exist_ok=True)
    earlier = case.backups / "bioaf_pre_update_2026.9.1_to_2026.10.1_x.sql.gz"
    earlier.write_bytes(b"")
    earlier.chmod(0o644)
    case.backups.chmod(0o755)

    done = case.run(*ENTRYPOINT_RUN)
    assert done.returncode == 0, done.stderr[-3000:]

    def mode(path):
        return stat.S_IMODE(path.stat().st_mode)

    [dump] = _dumps(case)
    assert mode(case.backups) == 0o700
    for path in (dump, dump.with_suffix(".json"), earlier):
        assert mode(path) == 0o600, path.name
        assert (path.stat().st_uid, path.stat().st_gid) == (case.backups.stat().st_uid, case.backups.stat().st_gid)
    assert os.access(dump, os.R_OK)


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
        # With the .env key that was in use (its fingerprint is in the manifest), secrets read back.
        manifest = json.loads(dump.with_suffix(".json").read_text())
        assert manifest["encryption_key_fingerprints"] == [
            hashlib.sha256(runner.TEST_ENCRYPTION_KEY.encode()).hexdigest()[:16]
        ]
        assert asyncio.run(_webhook(target)) == "https://hooks.example/lab"
    finally:
        asyncio.run(target.drop())


async def _webhook(case: CaseDatabase) -> str:
    conn = await case.connect()
    try:
        stored = await conn.fetchval("SELECT webhook_url FROM slack_webhooks")
    finally:
        await conn.close()
    return Fernet(runner.TEST_ENCRYPTION_KEY.encode()).decrypt(stored.encode()).decode()


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
        assert asyncio.run(_webhook(target)) == "https://hooks.example/lab"
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
            # Every migration records its revision; refusing that write fails whichever migration runs next.
            await conn.execute(
                "CREATE FUNCTION block_upgrade() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'blocked by test'; END $$"
            )
            await conn.execute(
                "CREATE TRIGGER block_upgrade BEFORE UPDATE ON alembic_version "
                "FOR EACH ROW EXECUTE FUNCTION block_upgrade()"
            )
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
            await conn.execute("DROP TRIGGER block_upgrade ON alembic_version")
            await conn.execute("DROP FUNCTION block_upgrade()")
        finally:
            await conn.close()

    asyncio.run(_unblock())
    retried = case.run(*ENTRYPOINT_RUN)
    assert retried.returncode == 0, retried.stderr[-3000:]
    assert _record(case)["status"] == "completed"
    assert len(_dumps(case)) == 2, "the retry makes its own recovery set and keeps the first"


@pytest.mark.asyncio
async def test_a_database_at_head_that_fails_validation_closes_the_gate(released, monkeypatch):
    """Validation on the nothing-to-migrate path records its failure too, so the gate stays shut."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import schema_upgrade
    from app.config import settings

    case, _ = released
    monkeypatch.setattr(settings, "update_status_dir", str(case.status))
    assert (await asyncio.to_thread(case.run, *ENTRYPOINT_RUN)).returncode == 0

    async def _sql(statement: str) -> None:
        conn = await case.connect()
        try:
            await conn.execute(statement)
        finally:
            await conn.close()

    await _sql("DROP INDEX uq_samples_experiment_external_id")
    failed = await asyncio.to_thread(case.run, *ENTRYPOINT_RUN)
    assert failed.returncode == 1
    record = _record(case)
    assert (record["status"], record["step"]) == ("failed", "validate")
    assert "uq_samples_experiment_external_id" in record["error"]
    engine = create_async_engine(case.url)
    try:
        stopped = await schema_upgrade.compatibility(engine)
        assert (stopped["compatible"], stopped["reason"]) == (False, "upgrade_failed")

        await _sql(
            "CREATE UNIQUE INDEX uq_samples_experiment_external_id ON samples (experiment_id, external_id) "
            "WHERE external_id IS NOT NULL"
        )
        assert (await asyncio.to_thread(case.run, *ENTRYPOINT_RUN)).returncode == 0
        assert _record(case)["status"] == "completed"
        assert (await schema_upgrade.compatibility(engine))["compatible"] is True
    finally:
        await engine.dispose()


def test_a_read_only_alembic_command_reports_without_upgrading(released):
    """`alembic current` (run by hand inside the container) names no destination; it must not fail or migrate."""
    case, release = released
    before = asyncio.run(_state(case))
    done = case.run("-m", "alembic", "-c", "alembic/alembic.ini", "current")
    assert done.returncode == 0, done.stderr[-3000:]
    assert release["head"] in done.stdout
    assert asyncio.run(_state(case)) == before
    assert _record(case) == {} and _dumps(case) == []


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


def test_inside_the_image_backups_must_be_on_a_mounted_volume(released):
    """A dump in the container's writable layer is gone when Compose replaces the container."""
    case, _ = released
    before = asyncio.run(_state(case))
    failed = case.run(*ENTRYPOINT_RUN, BIOAF_UPGRADE_REQUIRE_MOUNTED_BACKUPS="true")
    assert failed.returncode == 1
    assert "not a mounted volume" in _record(case)["error"]
    assert asyncio.run(_state(case)) == before


def test_status_reports_whether_this_images_upgrade_settled(released):
    """`./bioaf update` retries unless the running image says its own upgrade completed."""
    case, _ = released

    def _status():
        done = case.run("-m", "app.schema_upgrade", "status")
        assert done.returncode == 0
        return json.loads(done.stdout)

    assert _status()["settled"] is False
    assert case.run(*ENTRYPOINT_RUN, BIOAF_UPGRADE_PG_BIN_DIR="/nonexistent").returncode == 1
    assert _status()["settled"] is False
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    verdict = _status()
    assert verdict["settled"] is True and verdict["code_head"] == code_head()

    # A record an older image left (another head) is not this image's upgrade.
    record = _record(case)
    (case.status / "schema-upgrade.json").write_text(json.dumps({**record, "target_revision": "131"}))
    assert _status()["settled"] is False


def test_the_dump_checksum_streams_the_file_instead_of_loading_it(tmp_path):
    """A dump larger than the backend's memory limit must still hash: memory stays bounded."""
    import tracemalloc

    from app.schema_upgrade import file_sha256

    dump = tmp_path / "large.dump"
    with dump.open("wb") as f:
        f.write(b"bioaf")
        f.truncate(64 * 1024 * 1024)
    expected = hashlib.sha256()
    with dump.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            expected.update(block)

    tracemalloc.start()
    try:
        digest = file_sha256(dump)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert digest == expected.hexdigest()
    assert peak < 8 * 1024 * 1024


# --- GEO file association advisory (non-blocking) ------------------------------------------------------
#
# GEO export now takes Sample file ownership from links only. The upgrade reports legacy unlinked files
# that the old filename rule tied to Samples, but that report never blocks or fails the upgrade: it runs
# after the lock is released, and its own failure only marks the advisory incomplete. Runtime GEO
# readiness and download check every request regardless.

ADVISORY = "geo_association_advisory"


async def _populate_legacy_geo(case: CaseDatabase) -> dict:
    conn = await case.connect()
    try:
        org = await conn.fetchval("SELECT id FROM organizations WHERE name = 'Lab'")
        exp = await conn.fetchval("SELECT id FROM experiments WHERE organization_id = $1", org)

        async def file(org_id, exp_id, name, kind):
            uri = f"gs://raw/experiments/{exp_id}/{name}"
            return await conn.fetchval(
                "INSERT INTO files (organization_id, gcs_uri, storage_uri, filename, file_type) "
                "VALUES ($1, $2, $2, $3, $4) RETURNING id",
                org_id,
                uri,
                name,
                kind,
            )

        await file(org, exp, "S-0_R1.fastq.gz", "fastq.gz")  # unlinked, named after S-0: a finding
        linked = await file(org, exp, "lane.fastq.gz", "fastq.gz")
        s1 = await conn.fetchval("SELECT id FROM samples WHERE experiment_id = $1 AND external_id = 'S-1'", exp)
        await conn.execute("INSERT INTO sample_files (sample_id, file_id) VALUES ($1, $2)", s1, linked)
        await file(org, exp, "matrix.h5", "h5")  # unlinked, named after nobody: an Experiment-level asset

        other = await conn.fetchval("INSERT INTO organizations (name) VALUES ('Other lab') RETURNING id")
        other_exp = await conn.fetchval(
            "INSERT INTO experiments (organization_id, name, status) VALUES ($1, 'x', 'registered') RETURNING id", other
        )
        await conn.execute(
            "INSERT INTO samples (experiment_id, external_id, status, qc_status) VALUES ($1, 'T-1', 'registered', 'fail')",
            other_exp,
        )
        await file(other, other_exp, "T-1_counts.h5", "h5")  # matches only a failed Sample
        return {"links": await conn.fetch("SELECT * FROM sample_files ORDER BY id")}
    finally:
        await conn.close()


async def _geo_state(case: CaseDatabase) -> dict:
    conn = await case.connect()
    try:
        return {
            "links": await conn.fetch("SELECT * FROM sample_files ORDER BY id"),
            "acks": await conn.fetchval("SELECT count(*) FROM geo_file_association_acknowledgments"),
        }
    finally:
        await conn.close()


def _status(case: CaseDatabase) -> dict:
    done = case.run("-m", "app.schema_upgrade", "status")
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)


def test_legacy_geo_findings_are_reported_without_holding_up_an_older_updaters_upgrade(released):
    case, _ = released
    before = asyncio.run(_populate_legacy_geo(case))

    done = case.run()  # an older updater's own `alembic upgrade head`
    assert done.returncode == 0, done.stderr[-3000:]
    assert "need review before their GEO export" in done.stdout

    record = _record(case)
    assert record["status"] == "completed" and record["step"] == "validated"
    assert _status(case)["settled"] is True
    advisory = record[ADVISORY]
    assert advisory["status"] == "completed" and advisory["revision"] == code_head()
    assert advisory["policy_revision"] == 1 and advisory["completed_at"]
    assert advisory["organizations_scanned"] == 2
    assert advisory["unlinked_candidate_files"] == 3
    assert advisory["unresolved"]["exclude_failed"] == {
        "experiments": 1,
        "files": 1,
        "sample_pairs": 1,
        "raw_files": 1,
        "processed_files": 0,
    }
    assert advisory["unresolved"]["include_all"]["files"] == 2
    assert advisory["unresolved"]["include_all"]["processed_files"] == 1
    # The broadly readable record carries counts only.
    for secret in ("S-0_R1", "T-1_counts", "gs://", "matrix"):
        assert secret not in json.dumps(record)

    # The detail is in an operator-private report beside the backups.
    report = case.backups / advisory["report"]
    assert oct(report.stat().st_mode & 0o777) == "0o600"
    detail = json.loads(report.read_text())
    named = {
        f["filename"]
        for org in detail["organizations"]
        for exp in org["experiments"]
        for findings in exp["unresolved"].values()
        for f in findings
    }
    assert named == {"S-0_R1.fastq.gz", "T-1_counts.h5"}

    # Nothing scientific was rewritten: no links added, no acknowledgments, an empty table ready for them.
    after = asyncio.run(_geo_state(case))
    assert after == {"links": before["links"], "acks": 0}


def test_a_fresh_install_reports_no_geo_findings(case_db):
    done = case_db.run(*ENTRYPOINT_RUN)
    assert done.returncode == 0, done.stderr[-3000:]
    advisory = _record(case_db)[ADVISORY]
    assert advisory["status"] == "completed"
    assert advisory["unresolved"]["include_all"]["files"] == 0


def test_a_completed_advisory_is_not_repeated(released):
    case, _ = released
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    first = _record(case)
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    assert _record(case) == first
    assert len(list(case.backups.glob("bioaf_geo_association_advisory_*.json"))) == 1


def _mark_interrupted(case: CaseDatabase) -> None:
    """What an advisory killed mid-scan leaves behind: a completed upgrade, an advisory still running."""
    path = case.status / "schema-upgrade.json"
    record = json.loads(path.read_text())
    record[ADVISORY] = {**record[ADVISORY], "status": "running"}
    for key in ("unresolved", "completed_at", "report"):
        record[ADVISORY].pop(key, None)
    path.write_text(json.dumps(record))


def test_a_timed_out_advisory_leaves_the_upgrade_completed_and_is_retried(released):
    case, _ = released
    asyncio.run(_populate_legacy_geo(case))
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    _mark_interrupted(case)

    async def _run_while_links_are_locked():
        holder = await case.connect()
        try:
            async with holder.transaction():
                await holder.execute("LOCK TABLE sample_files IN ACCESS EXCLUSIVE MODE")
                return await asyncio.to_thread(
                    case.run, *ENTRYPOINT_RUN, BIOAF_GEO_ASSOCIATION_ADVISORY_TIMEOUT_SECONDS="1"
                )
        finally:
            await holder.close()

    done = asyncio.run(_run_while_links_are_locked())
    assert done.returncode == 0, done.stderr[-3000:]
    assert "GEO file association check did not complete" in done.stdout
    record = _record(case)
    assert record["status"] == "completed" and _status(case)["settled"] is True
    advisory = record[ADVISORY]
    assert advisory["status"] == "failed" and advisory["step"] == "scan" and advisory["error"]
    assert "unresolved" not in advisory  # an incomplete scan never reads as zero findings

    retried = case.run(*ENTRYPOINT_RUN)
    assert retried.returncode == 0, retried.stderr[-3000:]
    advisory = _record(case)[ADVISORY]
    assert advisory["status"] == "completed" and advisory["unresolved"]["include_all"]["files"] == 2


def test_an_unwritable_advisory_report_fails_only_the_advisory(released, tmp_path):
    case, _ = released
    asyncio.run(_populate_legacy_geo(case))
    assert case.run(*ENTRYPOINT_RUN).returncode == 0
    _mark_interrupted(case)
    not_a_directory = tmp_path / "backups-file"
    not_a_directory.write_text("")

    done = case.run(*ENTRYPOINT_RUN, BIOAF_UPGRADE_BACKUPS_DIR=str(not_a_directory))
    assert done.returncode == 0, done.stderr[-3000:]
    record = _record(case)
    assert record["status"] == "completed" and _status(case)["settled"] is True
    advisory = record[ADVISORY]
    assert advisory["status"] == "failed" and advisory["step"] == "report"
    assert "unresolved" not in advisory
