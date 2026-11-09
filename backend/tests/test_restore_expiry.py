"""Restore review expiry: a late Accept must never replace the primary with a stale review copy.

The timeout returns traffic to the primary; writes made after that belong to the primary. An Accept
that arrives after expiry (whether or not the timer has run, and whether or not its cleanup worked)
must be refused before any swap, rename, drop, or connection termination.

The control-flow tests replace the database swap and admin SQL. ``test_late_accept_on_a_real_server``
runs the real swap, admin SQL, pg_restore, and renames on a PostgreSQL server it provisions itself.
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.database as database_module
from app.config import settings
from app.services import backup_service
from app.services.backup_service import RestoreService
from tests.support import disposable_pg as dpg

PRIMARY = "postgresql+asyncpg://bioaf:pw@db:5432/bioaf"
# The service downloads to /tmp/<filename>; unique per test process so xdist workers never share it.
FILENAME = f"pgdump-{os.getpid() % 10**8:08d}-{random.randrange(10**6):06d}.dump"

pytestmark = pytest.mark.asyncio


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


class _FakeStorage:
    def __init__(self, source: str | None = None):
        self.source = source

    def build_uri(self, bucket: str, key: str) -> str:
        return f"fake://{bucket}/{key}"

    async def download_to_filename(self, uri: str, path: str) -> None:
        if self.source:
            with open(self.source, "rb") as src, open(path, "wb") as dst:
                dst.write(src.read())
        else:
            with open(path, "wb") as dst:
                dst.write(b"dump")


def _fake_external_start(monkeypatch, source: str | None = None) -> None:
    async def bucket(_session):
        return "backups"

    monkeypatch.setattr(backup_service, "_get_backups_bucket", bucket)
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: _FakeStorage(source))


@pytest.fixture
def harness(monkeypatch):
    """Swap and admin SQL recorded in order; pg_restore and storage replaced."""
    h = SimpleNamespace(calls=[], fail=None, gate=None, review_seconds=3600)
    monkeypatch.setattr(settings, "database_url", PRIMARY)

    async def swap(url: str) -> None:
        h.calls.append(("swap", url))
        await asyncio.sleep(0)  # the real swap suspends while it disposes the old pool

    async def admin(statements: list[str]) -> None:
        for stmt in statements:
            if h.gate is not None and "RENAME" in stmt:
                await h.gate.wait()
            if h.fail is not None and h.fail(stmt):
                h.calls.append(("failed", stmt))
                raise RuntimeError("admin statement failed")
            h.calls.append(("sql", stmt))

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def restore_exec(*args, **kwargs):
        return _Proc()

    async def databases() -> set[str]:
        return h.databases

    h.databases = {"postgres", "bioaf", "bioaf_restore"}
    monkeypatch.setattr(database_module, "swap_database", swap)
    monkeypatch.setattr(backup_service, "_run_admin_sql", admin)
    monkeypatch.setattr(backup_service, "_existing_databases", databases)
    monkeypatch.setattr(backup_service.asyncio, "create_subprocess_exec", restore_exec)
    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 3600)
    _fake_external_start(monkeypatch)
    return h


def _destructive(calls) -> list:
    return [c for c in calls if c[0] == "sql" and ("RENAME" in c[1] or "pg_terminate_backend" in c[1])]


async def _start(session=None) -> dict:
    return await RestoreService.start(session, org_id=1, filename=FILENAME)


def _since(h, mark: int) -> list:
    return h.calls[mark:]


async def test_accept_is_refused_after_the_timeout_returned_to_the_primary(harness, monkeypatch):
    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 0.05)
    assert (await _start())["status"] == "reviewing"

    await _until(lambda: not RestoreService.get_status()["active"])
    assert ("swap", PRIMARY) in harness.calls
    assert ("sql", "DROP DATABASE IF EXISTS bioaf_restore") in harness.calls

    mark = len(harness.calls)
    result = await RestoreService.accept()
    assert result["status"] == "error"
    assert _since(harness, mark) == []


async def test_accept_is_refused_once_the_deadline_passes_even_if_the_timer_has_not_run(harness, monkeypatch):
    assert (await _start())["status"] == "reviewing"
    later = datetime.now(timezone.utc) + timedelta(seconds=3601)
    monkeypatch.setattr(backup_service, "_now", lambda: later)

    mark = len(harness.calls)
    result = await RestoreService.accept()
    assert result["status"] == "error"
    assert "expired" in result["message"].lower()
    assert _since(harness, mark) == []


async def test_a_failed_revert_keeps_the_review_ineligible_and_reject_retries_it(harness, monkeypatch):
    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 0.05)
    assert (await _start())["status"] == "reviewing"
    harness.fail = lambda stmt: stmt == "DROP DATABASE IF EXISTS bioaf_restore"

    await _until(lambda: any(c[0] == "failed" for c in harness.calls))
    await asyncio.sleep(0.05)

    mark = len(harness.calls)
    accepted = await RestoreService.accept()
    assert accepted["status"] == "error"
    assert _since(harness, mark) == []
    # The review is not cleared while the database state is uncertain: no new restore starts.
    assert RestoreService.get_status()["active"] is True
    assert (await _start())["message"] == "A restore review is already active"

    harness.fail = None
    rejected = await RestoreService.reject()
    assert rejected["status"] == "reverted"
    assert RestoreService.get_status() == {"active": False}
    assert (await _start())["status"] == "reviewing"


async def test_a_new_restore_can_start_after_the_timeout_reverts(harness, monkeypatch):
    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 0.05)
    assert (await _start())["status"] == "reviewing"
    await _until(lambda: not RestoreService.get_status()["active"])

    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 3600)
    assert (await _start())["status"] == "reviewing"
    assert RestoreService.get_status()["active"] is True


async def test_an_accept_admitted_before_expiry_finishes_and_the_timer_does_not_revert(harness, monkeypatch):
    monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 0.2)
    assert (await _start())["status"] == "reviewing"
    harness.gate = asyncio.Event()

    accepting = asyncio.create_task(RestoreService.accept())
    await _until(lambda: any("pg_terminate_backend" in c[1] for c in harness.calls))
    await asyncio.sleep(0.4)  # the review deadline passes while the accept is mid-rename
    harness.gate.set()
    result = await accepting

    assert result["status"] == "accepted"
    await asyncio.sleep(0.05)
    assert ("sql", "DROP DATABASE IF EXISTS bioaf_restore") not in harness.calls[1:]
    assert RestoreService.get_status() == {"active": False}


async def test_an_unexpired_accept_renames_and_returns_to_the_primary_url(harness):
    assert (await _start())["status"] == "reviewing"
    mark = len(harness.calls)

    result = await RestoreService.accept()

    assert result["status"] == "accepted"
    assert [c[1] for c in _destructive(_since(harness, mark)) if "RENAME" in c[1]] == [
        "ALTER DATABASE bioaf RENAME TO bioaf_old",
        "ALTER DATABASE bioaf_restore RENAME TO bioaf",
    ]
    assert _since(harness, mark)[-1] == ("swap", PRIMARY)
    assert RestoreService.get_status() == {"active": False}


async def test_accept_parks_the_pool_on_postgres_with_the_same_user(harness):
    assert (await _start())["status"] == "reviewing"
    mark = len(harness.calls)

    await RestoreService.accept()

    assert _since(harness, mark)[0] == ("swap", "postgresql+asyncpg://bioaf:pw@db:5432/postgres")


async def test_a_second_decision_after_accept_is_refused(harness):
    assert (await _start())["status"] == "reviewing"
    harness.gate = asyncio.Event()

    accepting = asyncio.create_task(RestoreService.accept())
    await _until(lambda: any("pg_terminate_backend" in c[1] for c in harness.calls))
    rejecting = asyncio.create_task(RestoreService.reject())
    await asyncio.sleep(0.05)
    harness.gate.set()

    assert (await accepting)["status"] == "accepted"
    assert (await rejecting)["status"] == "error"
    assert ("swap", "postgresql+asyncpg://bioaf:pw@db:5432/bioaf_restore") not in harness.calls[-2:]


# --- a real server ---------------------------------------------------------------------------

needs_initdb = pytest.mark.skipif(dpg.find_pg_bin() is None, reason="PostgreSQL server binaries (initdb) not found")


def _sql(server, database: str, *statements: str) -> list:
    async def go():
        import asyncpg

        conn = await asyncpg.connect(
            host="127.0.0.1", port=server.port, user=server.user, database=database, timeout=10
        )
        try:
            rows = []
            for stmt in statements:
                rows = await conn.fetch(stmt)
            return [tuple(r) for r in rows]
        finally:
            await conn.close()

    return asyncio.run(go())


@needs_initdb
async def test_late_accept_on_a_real_server_keeps_the_primary_write(monkeypatch, tmp_path):
    """Timeout returns to the primary; a write lands there; a late Accept is refused and keeps it."""
    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    try:
        await asyncio.to_thread(
            _sql,
            server,
            server.database,
            "CREATE DATABASE bioaf",
        )
        await asyncio.to_thread(
            _sql,
            server,
            "bioaf",
            "CREATE TABLE notes (body text)",
            "INSERT INTO notes VALUES ('before backup')",
        )
        dump = tmp_path / FILENAME
        subprocess.run(
            [
                str(server.pg_bin / "pg_dump"),
                "-h",
                "127.0.0.1",
                "-p",
                str(server.port),
                "-U",
                server.user,
                "-Fc",
                "-f",
                str(dump),
                "bioaf",
            ],
            check=True,
            capture_output=True,
        )

        primary = f"postgresql+asyncpg://{server.user}@127.0.0.1:{server.port}/bioaf"
        monkeypatch.setattr(settings, "database_url", primary)
        monkeypatch.setenv("PATH", f"{server.pg_bin}{os.pathsep}{os.environ.get('PATH', '')}")
        monkeypatch.setattr(backup_service, "RESTORE_REVIEW_SECONDS", 1)
        _fake_external_start(monkeypatch, str(dump))
        # The swap replaces the module's engine; the monkeypatch puts the test process's back afterwards.
        monkeypatch.setattr(database_module, "engine", database_module.engine)
        monkeypatch.setattr(database_module, "async_session_factory", database_module.async_session_factory)
        await database_module.swap_database(primary)
        try:
            assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"
            await _until(lambda: not RestoreService.get_status()["active"], timeout=15)

            from sqlalchemy import text

            async with database_module.async_session_factory() as session:
                assert (await session.execute(text("SELECT current_database()"))).scalar() == "bioaf"
                await session.execute(text("INSERT INTO notes VALUES ('after timeout')"))
                await session.commit()

            result = await RestoreService.accept()
            assert result["status"] == "error"

            databases = {
                r[0] for r in await asyncio.to_thread(_sql, server, server.database, "SELECT datname FROM pg_database")
            }
            assert "bioaf_restore" not in databases
            assert "bioaf_old" not in databases
            notes = await asyncio.to_thread(_sql, server, "bioaf", "SELECT body FROM notes ORDER BY body")
            assert notes == [("after timeout",), ("before backup",)]
        finally:
            await database_module.engine.dispose()
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)


async def _prepare_real_primary(server, monkeypatch, tmp_path) -> None:
    """A `bioaf` database with one row, a dump of it served as the backup, and the pool on it."""
    await asyncio.to_thread(_sql, server, server.database, "CREATE DATABASE bioaf")
    await asyncio.to_thread(
        _sql, server, "bioaf", "CREATE TABLE notes (body text)", "INSERT INTO notes VALUES ('before backup')"
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
    monkeypatch.setenv("PATH", f"{server.pg_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    _fake_external_start(monkeypatch, str(dump))
    monkeypatch.setattr(database_module, "engine", database_module.engine)
    monkeypatch.setattr(database_module, "async_session_factory", database_module.async_session_factory)
    await database_module.swap_database(primary)


@needs_initdb
async def test_a_partial_rename_is_never_reported_as_a_successful_revert(monkeypatch, tmp_path):
    """Accept fails after `bioaf` became `bioaf_old`: reject must not drop the review copy, must not
    report success against a missing primary, and must leave work paused for an operator."""
    from app import runtime
    from app.runtime import TaskOwner

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    owner = TaskOwner()
    try:
        await _prepare_real_primary(server, monkeypatch, tmp_path)
        real_admin = backup_service._run_admin_sql

        async def fails_mid_rename(statements):
            for stmt in statements:
                if stmt == "ALTER DATABASE bioaf_restore RENAME TO bioaf":
                    raise RuntimeError("connection lost mid-rename")
                await real_admin([stmt])

        try:
            with runtime.bound(owner):
                assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"
                monkeypatch.setattr(backup_service, "_run_admin_sql", fails_mid_rename)
                assert (await RestoreService.accept())["status"] == "error"
                monkeypatch.setattr(backup_service, "_run_admin_sql", real_admin)

                rejected = await RestoreService.reject()
                accepted = await RestoreService.accept()

            assert rejected["status"] == "error"
            assert accepted["status"] == "error"
            databases = {
                r[0] for r in await asyncio.to_thread(_sql, server, server.database, "SELECT datname FROM pg_database")
            }
            assert {"bioaf_old", "bioaf_restore"} <= databases and "bioaf" not in databases
            assert owner.paused == "restore_review"
            assert RestoreService.get_status()["active"] is True
        finally:
            await database_module.engine.dispose()
            await owner.shutdown(drain_seconds=0, cancel_seconds=1)
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)


@needs_initdb
@pytest.mark.parametrize("reconnect_failures", [1, 2], ids=["retry-reconnects", "retry-fails"])
async def test_a_failure_after_the_replacement_completed_is_never_reverted(monkeypatch, tmp_path, reconnect_failures):
    """The renames and drop finished, then reconnecting failed. The replacement is live and the
    original is gone, so the review cannot reopen: it is either accepted (the reconnect retry
    worked) or needs recovery. Reject must never report a revert."""
    from app import runtime
    from app.runtime import TaskOwner

    provisioned = dpg.provision()
    server = await asyncio.to_thread(provisioned.__enter__)
    owner = TaskOwner()
    try:
        await _prepare_real_primary(server, monkeypatch, tmp_path)
        real_swap = database_module.swap_database
        state = {"renamed": False, "failures": reconnect_failures}
        real_admin = backup_service._run_admin_sql

        async def admin(statements):
            await real_admin(statements)
            if any("RENAME TO bioaf" in s and s.startswith("ALTER DATABASE bioaf_restore") for s in statements):
                state["renamed"] = True

        async def swap(url):
            if state["renamed"] and url.endswith("/bioaf") and state["failures"]:
                state["failures"] -= 1
                raise ConnectionError("could not reconnect")
            await real_swap(url)

        try:
            with runtime.bound(owner):
                assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"
                monkeypatch.setattr(backup_service, "_run_admin_sql", admin)
                monkeypatch.setattr(database_module, "swap_database", swap)
                accepted = await RestoreService.accept()
                rejected = await RestoreService.reject()

            databases = {
                r[0] for r in await asyncio.to_thread(_sql, server, server.database, "SELECT datname FROM pg_database")
            }
            assert "bioaf" in databases and "bioaf_restore" not in databases
            assert rejected["status"] == "error"
            if reconnect_failures == 1:
                assert accepted["status"] == "accepted"
                assert RestoreService.get_status() == {"active": False}
                assert owner.paused is None
            else:
                assert accepted["status"] == "error"
                assert RestoreService.get_status()["active"] is True
                assert owner.paused == "restore_review"
        finally:
            await database_module.engine.dispose()
            await owner.shutdown(drain_seconds=0, cancel_seconds=1)
    finally:
        await asyncio.to_thread(provisioned.__exit__, None, None, None)
