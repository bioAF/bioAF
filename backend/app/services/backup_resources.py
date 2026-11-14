"""Private workspaces and child processes for database backup creation and restore.

A database dump holds the whole installation, so one operation owns everything it creates and removes it:

- **Workspace.** ``private_workspace(kind)`` makes a fresh directory under ``settings.backup_scratch_dir``
  (0700, the root 0700 too); ``new_file`` creates fixed-name files inside it 0600 from creation. Request input
  (a backup's name) never becomes a local path. On exit the directory is removed; a cleanup failure is logged by
  type and never replaces the operation's own error. Entering also removes earlier ``<kind>-*`` directories: the
  caller holds that kind's one-at-a-time reservation, so any such directory was left by an operation that no longer
  runs (a crash or kill). Nothing else in the root, and nothing of another kind, is touched.
- **Child.** ``run_child`` runs ``pg_dump``/``pg_restore`` with a private umask, no stdin or stdout, and keeps only
  the tail of stderr, reduced to a safe category: stderr can hold SQL, object names and row data, so it never
  reaches a log, an audit record or a response. On cancellation or timeout the child is sent TERM, given
  ``stop_grace`` seconds, then KILL and ``kill_wait`` seconds; a child that still has not exited raises
  ``ChildNotStopped`` so the caller keeps its exclusion instead of deleting files under a live process.
- **Adapter work.** Storage adapters run blocking SDK calls in threads, which cancellation cannot stop.
  ``finish_before_cleanup`` waits for such work to finish before a cancellation propagates, so files are not deleted
  under a thread still writing them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from app.config import settings

logger = logging.getLogger("bioaf.backup_resources")

KINDS = frozenset({"backup", "restore"})
STDERR_TAIL_BYTES = 64 * 1024
STOP_GRACE_SECONDS = 10.0
KILL_WAIT_SECONDS = 5.0

T = TypeVar("T")


class ChildNotStopped(RuntimeError):
    """A child did not exit after TERM and KILL; its files and exclusion must be kept."""


@dataclass(frozen=True)
class ChildResult:
    """What a child did, without anything it printed. ``returncode`` is None when it never started."""

    returncode: int | None
    category: str
    timed_out: bool = False


class Workspace:
    def __init__(self, path: Path):
        self.path = path
        self.retained = False

    def retain(self) -> None:
        """Keep the workspace on exit: something may still use it (a child that could not be stopped)."""
        self.retained = True

    def new_file(self, name: str) -> Path:
        """Create an empty file in the workspace, private from creation; refuses an existing or escaping name."""
        if not name or name in (".", "..") or "/" in name or os.sep in name or "\0" in name:
            raise ValueError("a workspace file name is a plain file name")
        path = self.path / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        return path


def _remove(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("Could not remove a %s workspace (%s)", path.name.split("-", 1)[0], type(exc).__name__)


def _sweep(root: Path, kind: str) -> None:
    for entry in root.iterdir():
        if entry.name.startswith(f"{kind}-") and entry.is_dir() and not entry.is_symlink():
            logger.warning("Removing a %s workspace left by an earlier run", kind)
            _remove(entry)


@asynccontextmanager
async def private_workspace(kind: str, *, root: Path | str | None = None) -> AsyncIterator[Workspace]:
    """A private directory for one operation of ``kind``; the caller holds that kind's reservation."""
    if kind not in KINDS:
        raise ValueError(f"unknown workspace kind {kind!r}")
    base = Path(root if root is not None else settings.backup_scratch_dir)
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    base.chmod(0o700)
    await asyncio.to_thread(_sweep, base, kind)
    path = Path(tempfile.mkdtemp(prefix=f"{kind}-", dir=base))
    workspace = Workspace(path)
    try:
        yield workspace
    finally:
        if workspace.retained:
            logger.error("A %s workspace was retained for recovery; it is removed by the next %s", kind, kind)
        else:
            await asyncio.shield(asyncio.to_thread(_remove, path))


# Ordered: the first match names the category. Matching is on lowercased stderr and keeps nothing else.
_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("disk_full", ("no space left on device", "disk full", "could not extend file")),
    ("authentication", ("authentication failed", "no password supplied")),
    ("connection", ("connection refused", "could not connect", "could not translate host", "timeout expired")),
    ("version_mismatch", ("version mismatch", "unsupported version")),
    (
        "archive_unreadable",
        ("not appear to be a valid archive", "could not read input file", "unexpected end of file", "corrupt"),
    ),
    ("permission", ("permission denied", "must be owner")),
    ("sql_error", ("could not execute query", "error:")),
)


def classify_stderr(stderr: bytes) -> str:
    """A safe category for a PostgreSQL client's stderr; the text itself is never kept."""
    text = stderr.decode("utf-8", "replace").lower()
    if not text.strip():
        return "none"
    for category, needles in _CATEGORIES:
        if any(n in text for n in needles):
            return category
    return "unclassified"


async def _stop(proc, waiter: asyncio.Future, stop_grace: float, kill_wait: float) -> None:
    for signal_child, wait in ((proc.terminate, stop_grace), (proc.kill, kill_wait)):
        if waiter.done():
            return
        try:
            signal_child()
        except ProcessLookupError:
            pass
        done, _ = await asyncio.wait({waiter}, timeout=wait)
        if done:
            return
    raise ChildNotStopped(f"child process {proc.pid} did not exit after TERM and KILL")


async def run_child(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float | None = None,
    stop_grace: float = STOP_GRACE_SECONDS,
    kill_wait: float = KILL_WAIT_SECONDS,
) -> ChildResult:
    """Run one child to completion (or stop it on timeout or cancellation); see the module docstring."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            umask=0o077,
        )
    except FileNotFoundError:
        return ChildResult(None, "missing_binary")

    tail = bytearray()

    async def collect() -> int:
        stream = getattr(proc, "stderr", None)
        if stream is not None:
            while chunk := await stream.read(65536):
                tail.extend(chunk)
                del tail[:-STDERR_TAIL_BYTES]
        return await proc.wait()

    waiter = asyncio.ensure_future(collect())
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout)
    except asyncio.TimeoutError:
        await _stop(proc, waiter, stop_grace, kill_wait)
        return ChildResult(proc.returncode, classify_stderr(bytes(tail)), timed_out=True)
    except asyncio.CancelledError:
        await _stop(proc, waiter, stop_grace, kill_wait)
        raise
    return ChildResult(proc.returncode, classify_stderr(bytes(tail)))


async def finish_before_cleanup(work: Awaitable[T]) -> T:
    """Await ``work``; if cancelled meanwhile, wait for it to finish first, then propagate the cancellation."""
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # retrieved, so an error after cancellation is not reported as unhandled
        raise
