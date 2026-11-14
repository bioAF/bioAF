"""Restore preflight and strict execution (owner-approved restore decisions D4, D5, D6).

Before any DROP/CREATE of bioaf_restore:
- the backup's expected checksum and identity are read first, and the downloaded bytes must match them (a backup
  swapped after that read is refused, not re-read);
- an unmarked backup is restorable only if the cutover inventory names it and the request opts in, which is audited;
  anything else without a valid marker is refused;
- the dump is bounded by the configured size and scratch floor, and its table of contents must be readable.

Then pg_restore runs with --exit-on-error --single-transaction --no-owner --no-acl, and any nonzero exit fails the
restore before review; the primary is never swapped.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.services import backup_resources, backup_service
from app.services.backup_service import RestoreService
from tests.support.backup_storage import FakeBackupStorage
from tests.test_restore_expiry import FILENAME, harness  # noqa: F401 -- fixture

pytestmark = pytest.mark.asyncio


@pytest.fixture
def children(monkeypatch):
    """Record every child; ``results`` maps a marker in argv to the ChildResult it gets."""
    calls: list[list[str]] = []
    results: dict[str, backup_resources.ChildResult] = {}

    async def run_child(argv, **kwargs):
        calls.append(list(argv))
        for marker, result in results.items():
            if marker in argv:
                return result
        return backup_resources.ChildResult(0, "none")

    monkeypatch.setattr(backup_resources, "run_child", run_child)
    return calls, results


def _use(monkeypatch, storage: FakeBackupStorage) -> FakeBackupStorage:
    async def bucket(_session):
        return "backups"

    monkeypatch.setattr(backup_service, "_get_backups_bucket", bucket)
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: storage)
    return storage


def _ddl(h) -> list[str]:
    return [c[1] for c in h.calls if c[0] == "sql" and ("CREATE" in c[1] or "DROP" in c[1])]


async def _start(**kwargs):
    return await RestoreService.start(None, org_id=1, filename=FILENAME, **kwargs)


async def test_a_matching_checksummed_backup_is_restored_strictly(harness, monkeypatch, children):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    calls, _ = children
    assert (await _start())["status"] == "reviewing"
    listing, restore = calls
    assert Path(listing[0]).name == "pg_restore" and "--list" in listing
    assert Path(restore[0]).name == "pg_restore"
    for flag in ("--exit-on-error", "--single-transaction", "--no-owner", "--no-acl"):
        assert flag in restore
    assert not any(a.startswith(("-j", "--jobs")) for a in restore)


async def test_a_nonzero_import_fails_before_review_and_keeps_the_primary(harness, monkeypatch, children):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    _, results = children
    results["--single-transaction"] = backup_resources.ChildResult(1, "sql_error")
    result = await _start()
    assert result["status"] == "error" and result["reference"]
    assert not any(c[0] == "swap" for c in harness.calls)
    assert _ddl(harness)[-1] == "DROP DATABASE IF EXISTS bioaf_restore"
    assert RestoreService.get_status() == {"active": False}


async def test_a_timed_out_import_fails(harness, monkeypatch, children):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    _, results = children
    results["--single-transaction"] = backup_resources.ChildResult(-9, "none", timed_out=True)
    assert (await _start())["status"] == "error"
    assert not any(c[0] == "swap" for c in harness.calls)


async def test_bytes_that_do_not_match_the_recorded_checksum_are_refused_before_ddl(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage())
    storage.replace_after_metadata = b"PGDMP different bytes"
    result = await _start()
    assert result["status"] == "error" and "checksum" in result["message"]
    assert _ddl(harness) == [] and children[0] == []


async def test_a_marker_naming_another_backup_is_refused(harness, monkeypatch, children):  # noqa: F811
    from app.services import backup_integrity as bi

    _use(monkeypatch, FakeBackupStorage(metadata=bi.marker("pgdump-20200101-000000.dump", "c" * 64)))
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "integrity"
    assert _ddl(harness) == []


async def test_an_unmarked_backup_outside_the_inventory_is_refused_even_with_the_opt_in(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage(metadata={}, legacy=[]))
    result = await _start(allow_unverified=True)
    assert result["status"] == "error" and result["reason"] == "integrity"
    assert storage.downloads == [] and _ddl(harness) == []


async def test_a_legacy_backup_needs_the_explicit_opt_in(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage(metadata={}, legacy=[FILENAME]))
    refused = await _start()
    assert refused["status"] == "error" and refused["reason"] == "unverified"
    assert storage.downloads == [] and _ddl(harness) == []


async def test_an_opted_in_legacy_restore_is_audited_and_never_called_verified(harness, monkeypatch, children, caplog):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage(metadata={}, legacy=[FILENAME]))
    audits = []

    async def log_action(session, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(backup_service, "log_action", log_action)
    caplog.set_level(logging.INFO)
    result = await _start(allow_unverified=True, user_id=7)
    assert result["status"] == "reviewing"
    (audit,) = audits
    assert audit["action"] == "restore_unverified_authorized" and audit["user_id"] == 7
    assert audit["details"]["filename"] == FILENAME
    assert "unverified" in caplog.text and "checksum verified" not in caplog.text


async def test_a_dump_over_the_size_limit_is_refused_before_download(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage(content=b"PGDMP" + b"x" * 100))
    monkeypatch.setattr(settings, "restore_max_dump_bytes", 50)
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit"
    assert storage.downloads == [] and _ddl(harness) == []


async def test_a_dump_that_grows_past_the_limit_while_downloading_is_refused(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage(content=b"PGDMP" + b"x" * 40))
    real = storage.get_object_metadata

    async def understated(uri):
        meta = await real(uri)
        return meta.model_copy(update={"size_bytes": 10})

    storage.get_object_metadata = understated
    monkeypatch.setattr(settings, "restore_max_dump_bytes", 30)
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit"
    assert _ddl(harness) == []


async def test_insufficient_scratch_space_is_refused_before_download(harness, monkeypatch, children):  # noqa: F811
    storage = _use(monkeypatch, FakeBackupStorage())
    monkeypatch.setattr(settings, "restore_scratch_floor_percent", 100)
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit"
    assert storage.downloads == [] and _ddl(harness) == []


async def test_an_unreadable_archive_is_refused_before_ddl(harness, monkeypatch, children):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    _, results = children
    results["--list"] = backup_resources.ChildResult(1, "archive_unreadable")
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "integrity"
    assert _ddl(harness) == []


async def test_the_whole_preparation_has_a_deadline(harness, monkeypatch, children):  # noqa: F811
    import asyncio

    storage = _use(monkeypatch, FakeBackupStorage())
    storage.release = asyncio.Event()  # the download never finishes
    monkeypatch.setattr(settings, "restore_preparation_timeout_seconds", 0.2)
    result = await _start()
    assert result["status"] == "error"
    assert _ddl(harness) == []
    assert RestoreService.get_status() == {"active": False}
    storage.release.set()


async def test_refusals_map_to_client_errors(harness, monkeypatch, children, client, admin_token):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage(metadata={}, legacy=[FILENAME]))
    headers = {"Authorization": f"Bearer {admin_token}"}
    refused = await client.post("/api/backups/restore/postgres", json={"filename": FILENAME}, headers=headers)
    assert refused.status_code == 400 and "unverified" in refused.json()["detail"].lower()
    accepted = await client.post(
        "/api/backups/restore/postgres", json={"filename": FILENAME, "allow_unverified": True}, headers=headers
    )
    assert accepted.status_code == 200


# --- a real PostgreSQL 16 server ------------------------------------------------------------------------------

import asyncio  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402

import app.database as database_module  # noqa: E402
from tests.test_restore_expiry import _sql, needs_initdb  # noqa: E402


async def _real_primary(server, monkeypatch, tmp_path) -> Path:
    """A `bioaf` database with enough rows that its data blocks lie past the archive's table of contents."""
    await asyncio.to_thread(_sql, server, server.database, "CREATE DATABASE bioaf")
    await asyncio.to_thread(
        _sql,
        server,
        "bioaf",
        "CREATE TABLE notes (body text)",
        "INSERT INTO notes VALUES ('primary row')",
        "CREATE TABLE bulk (id int, payload text)",
        "INSERT INTO bulk SELECT g, md5(g::text) FROM generate_series(1, 50000) g",
    )
    dump = tmp_path / FILENAME
    subprocess.run(
        [str(server.pg_bin / "pg_dump"), "-h", "127.0.0.1", "-p", str(server.port), "-U", server.user]
        + ["-Fc", "-f", str(dump), "bioaf"],
        check=True,
        capture_output=True,
    )
    primary = f"postgresql+asyncpg://{server.user}@127.0.0.1:{server.port}/bioaf"
    monkeypatch.setattr(settings, "database_url", primary)
    monkeypatch.setattr(settings, "upgrade_pg_bin_dir", str(server.pg_bin))
    monkeypatch.setenv("PATH", f"{server.pg_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(database_module, "engine", database_module.engine)
    monkeypatch.setattr(database_module, "async_session_factory", database_module.async_session_factory)
    await database_module.swap_database(primary)
    return dump


def _corrupt_data_block(dump: Path) -> None:
    data = bytearray(dump.read_bytes())
    at = len(data) * 4 // 5
    data[at : at + 2048] = os.urandom(2048)
    dump.write_bytes(bytes(data))


async def _databases(server) -> set[str]:
    return {r[0] for r in await asyncio.to_thread(_sql, server, server.database, "SELECT datname FROM pg_database")}


@needs_initdb
async def test_a_damaged_data_block_fails_the_import_and_keeps_the_primary(monkeypatch, tmp_path):
    """The digest matches the damaged bytes (as if damaged before it was recorded), so only the import can
    notice: pg_restore --list still reads the TOC, the strict import fails, and nothing reaches review."""
    from tests.support import disposable_pg as dpg

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    try:
        dump = await _real_primary(server, monkeypatch, tmp_path)
        _corrupt_data_block(dump)
        _use(monkeypatch, FakeBackupStorage(str(dump)))
        try:
            result = await _start()
            assert result["status"] == "error" and result["reference"]
            assert RestoreService.get_status() == {"active": False}
            assert "bioaf_restore" not in await _databases(server)
            notes = await asyncio.to_thread(_sql, server, "bioaf", "SELECT body FROM notes")
            assert notes == [("primary row",)]
        finally:
            await database_module.engine.dispose()
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)


@needs_initdb
async def test_swapped_bytes_are_refused_before_the_review_database_exists(monkeypatch, tmp_path):
    from tests.support import disposable_pg as dpg

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    try:
        dump = await _real_primary(server, monkeypatch, tmp_path)
        storage = _use(monkeypatch, FakeBackupStorage(str(dump)))
        damaged = tmp_path / "damaged.dump"
        damaged.write_bytes(dump.read_bytes())
        _corrupt_data_block(damaged)
        storage.replace_after_metadata = damaged.read_bytes()
        try:
            result = await _start()
            assert result["status"] == "error" and result["reason"] == "integrity"
            assert "bioaf_restore" not in await _databases(server)
        finally:
            await database_module.engine.dispose()
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)


@needs_initdb
async def test_an_intact_backup_restores_every_row(monkeypatch, tmp_path):
    from tests.support import disposable_pg as dpg

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    try:
        dump = await _real_primary(server, monkeypatch, tmp_path)
        _use(monkeypatch, FakeBackupStorage(str(dump)))
        try:
            assert (await _start())["status"] == "reviewing"
            rows = await asyncio.to_thread(_sql, server, "bioaf_restore", "SELECT count(*) FROM bulk")
            assert rows == [(50000,)]
            assert (await RestoreService.reject())["status"] == "reverted"
        finally:
            await database_module.engine.dispose()
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)


async def test_the_scratch_floor_is_enforced_during_the_download(harness, monkeypatch, children):  # noqa: F811
    """Free space that falls below the floor mid-download stops it before the disk is exhausted, before DDL."""
    from app.services import backup_resources as br

    big = b"PGDMP" + b"x" * (3 * br.SPACE_CHECK_BYTES)
    storage = _use(monkeypatch, FakeBackupStorage(content=big))

    class Writes:
        written = 0

    real_download = storage.download_to_file

    async def chunked(uri, file_obj):
        storage.downloads.append(uri)
        for i in range(0, len(big), br.SPACE_CHECK_BYTES // 2):
            chunk = big[i : i + br.SPACE_CHECK_BYTES // 2]
            file_obj.write(chunk)
            Writes.written += len(chunk)

    storage.download_to_file = chunked
    gib = 1024**3

    def usage(_path):
        # 100 GiB disk, floor 10 GiB: plenty before the download, nearly none once a few MiB have landed.
        free = 50 * gib if Writes.written < br.SPACE_CHECK_BYTES else 5 * gib
        return br.shutil._ntuple_diskusage(100 * gib, 100 * gib - free, free)

    monkeypatch.setattr(br.shutil, "disk_usage", usage)
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit"
    assert Writes.written < len(big)
    assert _ddl(harness) == []
    del real_download


# --- database-volume headroom (review follow-up 3) -----------------------------------------------------------


def _disk(monkeypatch, *, scratch_free_gib: float, db_free_gib: float, db_dir: str, after_bytes=None):
    """Path-aware disk usage: the database volume and the scratch disk report their own free space."""
    from app.services import backup_resources as br

    gib = 1024**3
    state = {"db_free": db_free_gib}

    def usage(path):
        free = state["db_free"] if str(path).startswith(db_dir) else scratch_free_gib
        return br.shutil._ntuple_diskusage(100 * gib, int((100 - free) * gib), int(free * gib))

    monkeypatch.setattr(br.shutil, "disk_usage", usage)
    return state


@pytest.fixture
def db_volume(monkeypatch, tmp_path):
    path = tmp_path / "db-volume"
    path.mkdir()
    monkeypatch.setattr(settings, "restore_db_volume_dir", str(path))
    monkeypatch.setattr(backup_service, "_primary_database_bytes", _const(2 * 1024**3))
    return str(path)


def _const(value):
    async def f():
        return value

    return f


async def test_a_database_volume_without_room_is_refused_before_ddl(harness, monkeypatch, children, db_volume):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    # 100 GiB volume, floor 10 GiB; needs 2 x 2 GiB = 4 GiB: 12 GiB free is short.
    _disk(monkeypatch, scratch_free_gib=80, db_free_gib=12, db_dir=db_volume)
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit" and "database" in result["message"]
    assert _ddl(harness) == []


async def test_a_database_volume_with_room_restores(harness, monkeypatch, children, db_volume):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    _disk(monkeypatch, scratch_free_gib=80, db_free_gib=20, db_dir=db_volume)
    assert (await _start())["status"] == "reviewing"


async def test_a_configured_volume_that_is_not_visible_is_refused(harness, monkeypatch, children, tmp_path):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    monkeypatch.setattr(settings, "restore_db_volume_dir", str(tmp_path / "not-mounted"))
    result = await _start()
    assert result["status"] == "error" and result["reason"] == "limit"
    assert _ddl(harness) == []


async def test_an_unconfigured_volume_is_reported_as_not_measured(harness, monkeypatch, children, caplog):  # noqa: F811
    _use(monkeypatch, FakeBackupStorage())
    monkeypatch.setattr(settings, "restore_db_volume_dir", "")
    caplog.set_level(logging.WARNING)
    assert (await _start())["status"] == "reviewing"
    assert "not measured" in caplog.text


async def test_the_import_stops_when_the_database_volume_runs_low(harness, monkeypatch, db_volume):  # noqa: F811
    """Free space can fall while pg_restore runs (WAL, other writers): the import is stopped above the floor."""
    import asyncio

    from app.services import backup_resources as br

    _use(monkeypatch, FakeBackupStorage())
    state = _disk(monkeypatch, scratch_free_gib=80, db_free_gib=20, db_dir=db_volume)
    monkeypatch.setattr(br, "WATCH_SECONDS", 0.05)
    real_exec = asyncio.subprocess.create_subprocess_exec

    async def slow_import(*argv, **kwargs):
        if "--single-transaction" in argv:
            state["db_free"] = 5  # the volume fills while the import runs
            return await real_exec(sys.executable, "-c", "import time; time.sleep(30)", **kwargs)
        return await real_exec(sys.executable, "-c", "pass", **kwargs)

    monkeypatch.setattr(backup_service.asyncio, "create_subprocess_exec", slow_import)
    result = await asyncio.wait_for(_start(), 20)
    assert result["status"] == "error" and result["reason"] == "limit"
    assert _ddl(harness)[-1] == "DROP DATABASE IF EXISTS bioaf_restore"
    assert not any(c[0] == "swap" for c in harness.calls)


@needs_initdb
async def test_the_database_volume_check_reads_the_real_primary_size(monkeypatch, tmp_path):
    """On a real server: the primary's size is read and a restore with room proceeds; without room it is refused."""
    from tests.support import disposable_pg as dpg

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    try:
        dump = await _real_primary(server, monkeypatch, tmp_path)
        _use(monkeypatch, FakeBackupStorage(str(dump)))
        volume = tmp_path / "db-volume"
        volume.mkdir()
        monkeypatch.setattr(settings, "restore_db_volume_dir", str(volume))
        size = await backup_service._primary_database_bytes()
        assert size > 1024 * 1024
        try:
            _disk(monkeypatch, scratch_free_gib=80, db_free_gib=10.5, db_dir=str(volume))  # floor 10 GiB
            monkeypatch.setattr(settings, "restore_scratch_floor_min_bytes", 0)
            monkeypatch.setattr(settings, "restore_scratch_floor_percent", 10)
            assert (await _start())["status"] == "reviewing"
            assert (await RestoreService.reject())["status"] == "reverted"
            _disk(monkeypatch, scratch_free_gib=80, db_free_gib=10.0, db_dir=str(volume))
            refused = await _start()
            assert refused["status"] == "error" and refused["reason"] == "limit"
            assert "bioaf_restore" not in await _databases(server)
        finally:
            await database_module.engine.dispose()
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)
