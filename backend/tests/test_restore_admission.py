"""Restore preparation: admission, private files, and safe diagnostics (RestoreService.start).

- Admission is taken before the first await, so of two concurrent starts exactly one prepares; the other does no
  download, database DDL, subprocess or cleanup.
- The dump is downloaded into a private workspace under a name the code chooses, and the workspace is gone when the
  start returns, whether it succeeded or failed.
- pg_restore's stderr and exception text never reach the response or the logs; a failure carries an operation
  reference that the log lines share.
- A cancelled start stops its child, drops the review copy, and releases admission; if the child cannot be stopped,
  admission is kept so nothing new runs beside it.

The swap, admin SQL and pg_restore are replaced (tests/test_restore_expiry.py's harness); storage is replaced here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.services import backup_resources, backup_service
from app.services.backup_service import RestoreService
from tests.test_restore_expiry import FILENAME, harness  # noqa: F401 -- fixture

pytestmark = pytest.mark.asyncio

SENTINEL = "sentinel-7c41-ssn-123-45-6789"


class _Storage:
    """Records downloads; can hold one open until released, and checks where and how it writes."""

    def __init__(self):
        self.downloads: list[str] = []
        self.paths: list[Path] = []
        self.modes: list[int] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.fail: Exception | None = None

    def build_uri(self, bucket: str, key: str) -> str:
        return f"fake://{bucket}/{key}"

    async def download_to_filename(self, uri: str, path: str) -> None:
        self.downloads.append(uri)
        self.paths.append(Path(path))
        self.modes.append(stat.S_IMODE(os.stat(Path(path).parent).st_mode))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail is not None:
            raise self.fail
        with open(path, "wb") as f:
            f.write(b"PGDMP fake dump")


@pytest.fixture
def storage(monkeypatch, harness):  # noqa: F811
    s = _Storage()
    monkeypatch.setattr("app.adapters.registry.get_storage_adapter", lambda: s)
    return s


def _effects(h) -> list:
    return [c for c in h.calls if c[0] in ("sql", "failed")]


async def test_two_concurrent_starts_admit_exactly_one(harness, storage):  # noqa: F811
    storage.release = asyncio.Event()
    spawned = []
    real_exec = backup_service.asyncio.create_subprocess_exec

    async def counting_exec(*args, **kwargs):
        spawned.append(args[0])
        return await real_exec(*args, **kwargs)

    backup_service.asyncio.create_subprocess_exec = counting_exec
    try:
        first = asyncio.create_task(RestoreService.start(None, org_id=1, filename=FILENAME))
        await storage.started.wait()
        effects_before = list(harness.calls)
        second = await RestoreService.start(None, org_id=1, filename=FILENAME)
        assert second["status"] == "error"
        assert len(storage.downloads) == 1  # the loser downloaded nothing
        assert harness.calls == effects_before  # and ran no DDL, cleanup or swap
        assert spawned == []
        storage.release.set()
        assert (await first)["status"] == "reviewing"
    finally:
        backup_service.asyncio.create_subprocess_exec = real_exec


async def test_a_busy_start_is_reported_as_a_conflict(harness, storage, client, admin_token):  # noqa: F811
    storage.release = asyncio.Event()
    first = asyncio.create_task(RestoreService.start(None, org_id=1, filename=FILENAME))
    await storage.started.wait()
    response = await client.post(
        "/api/backups/restore/postgres",
        json={"filename": FILENAME},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    storage.release.set()
    await first


async def test_the_dump_is_private_named_by_the_code_and_removed_after_success(harness, storage):  # noqa: F811
    assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"
    (path,) = storage.paths
    root = Path(settings.backup_scratch_dir)
    assert path.parent.parent == root and path.parent.name.startswith("restore-")
    assert FILENAME not in str(path)
    assert storage.modes == [0o700]
    assert not path.parent.exists()


async def test_the_workspace_is_removed_and_admission_released_after_a_failure(harness, storage):  # noqa: F811
    storage.fail = RuntimeError(f"download failed for {SENTINEL}")
    result = await RestoreService.start(None, org_id=1, filename=FILENAME)
    assert result["status"] == "error"
    assert list(Path(settings.backup_scratch_dir).iterdir()) == []
    storage.fail = None
    assert (await RestoreService.start(None, org_id=1, filename=FILENAME))["status"] == "reviewing"


async def test_restore_stderr_never_reaches_the_response_or_the_logs(harness, storage, monkeypatch, caplog):  # noqa: F811
    """A real child prints SQL with a sentinel to stderr and exits nonzero."""
    real_exec = asyncio.subprocess.create_subprocess_exec  # the harness replaced asyncio.create_subprocess_exec
    script = (
        f"import sys; sys.stderr.write('pg_restore: error: could not execute query: SELECT {SENTINEL}'); sys.exit(1)"
    )

    async def noisy_exec(*args, **kwargs):
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(backup_service.asyncio, "create_subprocess_exec", noisy_exec)
    caplog.set_level(logging.DEBUG)
    result = await RestoreService.start(None, org_id=1, filename=FILENAME)
    assert SENTINEL not in str(result) and SENTINEL not in caplog.text
    assert "sql_error" in caplog.text


async def test_a_failure_reports_an_operation_reference_without_exception_text(harness, storage, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    original = backup_service._run_admin_sql

    async def leaky(statements):
        if any(s.startswith("CREATE DATABASE") for s in statements):
            raise RuntimeError(f'database "bioaf_restore" {SENTINEL} password=hunter2')
        return await original(statements)

    backup_service._run_admin_sql = leaky
    try:
        result = await RestoreService.start(None, org_id=1, filename=FILENAME)
    finally:
        backup_service._run_admin_sql = original
    assert result["status"] == "error"
    reference = result["reference"]
    assert reference and reference in result["message"]
    assert SENTINEL not in str(result) and "hunter2" not in str(result)
    assert SENTINEL not in caplog.text and "hunter2" not in caplog.text
    assert reference in caplog.text and "RuntimeError" in caplog.text


async def test_a_cancelled_start_stops_its_child_drops_the_copy_and_releases_admission(
    harness,  # noqa: F811
    storage,
    monkeypatch,
):
    started = asyncio.Event()
    stopped = []

    async def hanging_child(argv, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append(argv[0])
            raise

    monkeypatch.setattr(backup_resources, "run_child", hanging_child)
    task = asyncio.create_task(RestoreService.start(None, org_id=1, filename=FILENAME))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped == ["pg_restore"]
    assert ("sql", "DROP DATABASE IF EXISTS bioaf_restore") in harness.calls[-1:]
    assert list(Path(settings.backup_scratch_dir).iterdir()) == []
    assert RestoreService.get_status() == {"active": False}


async def test_a_child_that_cannot_be_stopped_keeps_admission(harness, storage, monkeypatch):  # noqa: F811
    async def unstoppable(argv, **kwargs):
        raise backup_resources.ChildNotStopped("pid 1")

    monkeypatch.setattr(backup_resources, "run_child", unstoppable)
    first = await RestoreService.start(None, org_id=1, filename=FILENAME)
    assert first["status"] == "error"
    downloads = len(storage.downloads)
    second = await RestoreService.start(None, org_id=1, filename=FILENAME)
    assert second["status"] == "error" and len(storage.downloads) == downloads
    statements = [c[1] for c in harness.calls if c[0] == "sql"]
    # Only preparation's own DROP before CREATE: nothing drops the copy a live pg_restore may still write.
    assert statements == ["DROP DATABASE IF EXISTS bioaf_restore", "CREATE DATABASE bioaf_restore"]
