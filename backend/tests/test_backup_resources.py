"""Private workspaces and child processes for database backup creation and restore (app/services/backup_resources.py).

One operation owns its directory, files, child process and cleanup: the directory is private (0700), files are
private from creation (0600) and never named from request input, a child is stopped and waited for before its files
are deleted, cleanup never masks the operation's own error, and only workspaces of the caller's own kind are swept.
"""

from __future__ import annotations

import asyncio
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services import backup_resources as br

pytestmark = pytest.mark.asyncio


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- workspaces --------------------------------------------------------------------------------------------


async def test_a_workspace_is_private_and_its_files_are_private_from_creation(tmp_path):
    root = tmp_path / "scratch"
    async with br.private_workspace("restore", root=root) as ws:
        assert ws.path.parent == root and ws.path.name.startswith("restore-")
        assert _mode(root) == 0o700 and _mode(ws.path) == 0o700
        dump = ws.new_file("backup.dump")
        assert dump.parent == ws.path and _mode(dump) == 0o600 and dump.stat().st_size == 0
        with pytest.raises(FileExistsError):
            ws.new_file("backup.dump")
    assert not ws.path.exists()


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".", ".."])
async def test_a_workspace_file_name_cannot_leave_the_workspace(tmp_path, name):
    async with br.private_workspace("backup", root=tmp_path) as ws:
        with pytest.raises(ValueError):
            ws.new_file(name)


async def test_a_workspace_is_removed_when_the_operation_fails(tmp_path):
    with pytest.raises(RuntimeError, match="primary"):
        async with br.private_workspace("backup", root=tmp_path) as ws:
            ws.new_file("backup.dump").write_bytes(b"x" * 10)
            raise RuntimeError("primary")
    assert list(tmp_path.iterdir()) == []


async def test_a_cleanup_failure_does_not_mask_the_operations_error(tmp_path, monkeypatch, caplog):
    def failing_rmtree(path, *a, **k):
        raise PermissionError("cleanup")

    monkeypatch.setattr(br.shutil, "rmtree", failing_rmtree)
    with pytest.raises(RuntimeError, match="primary"):
        async with br.private_workspace("backup", root=tmp_path):
            raise RuntimeError("primary")
    assert "PermissionError" in caplog.text


async def test_only_stale_workspaces_of_the_same_kind_are_swept(tmp_path):
    stale_restore = tmp_path / "restore-old"
    stale_restore.mkdir()
    (stale_restore / "backup.dump").write_text("old")
    other_kind = tmp_path / "backup-active"
    other_kind.mkdir()
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("not ours")
    unrelated_dir = tmp_path / "restore_like_but_not"
    unrelated_dir.mkdir()

    async with br.private_workspace("restore", root=tmp_path) as ws:
        assert not stale_restore.exists()
        assert other_kind.exists() and unrelated.exists() and unrelated_dir.exists()
        assert ws.path.exists()


async def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        async with br.private_workspace("../etc", root=tmp_path):
            pass


# --- child processes ---------------------------------------------------------------------------------------


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def test_a_child_reports_its_exit_status_and_a_safe_category_only(tmp_path):
    secret = "SELECT secret_column FROM sentinel_table_93c1"
    result = await br.run_child(
        _python(f"import sys; sys.stderr.write('pg_restore: error: could not execute query: {secret}'); sys.exit(1)"),
        env=dict(os.environ),
    )
    assert result.returncode == 1
    assert result.category == "sql_error"
    assert secret not in repr(result) and secret not in str(result.__dict__)


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ('pg_dump: error: connection to server at "db" failed: Connection refused', "connection"),
        ('FATAL:  password authentication failed for user "bioaf_app"', "authentication"),
        ("pg_restore: error: could not write to output file: No space left on device", "disk_full"),
        ("pg_dump: error: aborting because of server version mismatch", "version_mismatch"),
        ("pg_restore: error: input file does not appear to be a valid archive", "archive_unreadable"),
        ("pg_restore: error: permission denied for schema public", "permission"),
        ("something nobody anticipated", "unclassified"),
        ("", "none"),
    ],
)
async def test_stderr_is_reduced_to_a_category(stderr, category):
    assert br.classify_stderr(stderr.encode()) == category


async def test_a_cancelled_operation_stops_and_waits_for_its_child(tmp_path):
    marker = tmp_path / "pid"
    task = asyncio.create_task(
        br.run_child(
            _python(f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(60)"),
            env=dict(os.environ),
        )
    )
    deadline = time.monotonic() + 10
    while not marker.exists() or not marker.read_text():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    pid = int(marker.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_child_that_ignores_terminate_is_killed_after_the_grace(tmp_path):
    code = (
        "import signal,time,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "sys.stdout.write('ready'); sys.stdout.flush(); time.sleep(60)"
    )
    started = time.monotonic()
    result = await br.run_child(_python(code), env=dict(os.environ), timeout=0.5, stop_grace=0.5, kill_wait=5)
    assert result.timed_out and result.returncode == -signal.SIGKILL
    assert time.monotonic() - started < 10


async def test_a_child_that_cannot_be_reaped_is_reported_as_not_stopped(monkeypatch):
    class Stuck:
        returncode = None
        pid = 999999

        def terminate(self):
            pass

        def kill(self):
            pass

        async def wait(self):
            await asyncio.Event().wait()

        async def communicate(self):
            await asyncio.Event().wait()

    async def spawn(*args, **kwargs):
        return Stuck()

    monkeypatch.setattr(br.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(br.ChildNotStopped):
        await br.run_child(["pg_restore"], env={}, timeout=0.05, stop_grace=0.05, kill_wait=0.05)


async def test_a_missing_binary_is_its_own_category():
    result = await br.run_child(["/nonexistent/pg_dump"], env={})
    assert result.returncode is None and result.category == "missing_binary"


async def test_children_write_private_files(tmp_path):
    out = tmp_path / "made-by-child"
    result = await br.run_child(_python(f"open({str(out)!r},'w').write('x')"), env=dict(os.environ))
    assert result.returncode == 0 and _mode(out) == 0o600


# --- adapter work ------------------------------------------------------------------------------------------


async def test_cancellation_waits_for_adapter_work_to_finish_before_returning():
    release = threading.Event()
    finished = threading.Event()

    def blocking_download():
        release.wait(5)
        finished.set()

    task = asyncio.create_task(br.finish_before_cleanup(asyncio.to_thread(blocking_download)))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()  # still waiting for the thread
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_adapter_work_returns_its_result_and_raises_its_error():
    async def ok():
        return 7

    async def bad():
        raise LookupError("x")

    assert await br.finish_before_cleanup(ok()) == 7
    with pytest.raises(LookupError):
        await br.finish_before_cleanup(bad())
