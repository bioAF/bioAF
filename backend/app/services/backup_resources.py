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
- **Download.** ``bounded_download`` streams an object into a workspace file through a writer that counts the
  bytes actually written and refuses past ``max_bytes`` or after its deadline, so the SDK's thread stops at its
  next write. ``require_scratch`` refuses unless the free space left after ``needed`` bytes stays above
  ``max(floor_min, floor_percent% of the filesystem)``.
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
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from app import runtime
from app.config import settings

logger = logging.getLogger("bioaf.backup_resources")

KINDS = frozenset({"backup", "restore"})
STDERR_TAIL_BYTES = 64 * 1024
STOP_GRACE_SECONDS = 10.0
# A download rechecks free space each time this many more bytes have been written (and before the first).
SPACE_CHECK_BYTES = 8 * 1024 * 1024
# A running child's ``watch`` (e.g. the database volume's free space) is called this often.
WATCH_SECONDS = 5.0
KILL_WAIT_SECONDS = 5.0

T = TypeVar("T")


class ResourceLimit(RuntimeError):
    """A bound was reached (``category``: dump_too_large, download_timeout, scratch_space)."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


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
            await runtime.shielded(asyncio.to_thread(_remove, path), role=f"{kind}-workspace-cleanup")


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
    watch: Callable[[], None] | None = None,
) -> ChildResult:
    """Run one child to completion (or stop it on timeout or cancellation); see the module docstring.

    ``watch`` runs every WATCH_SECONDS while the child runs; a ``ResourceLimit`` it raises stops the child and
    is raised to the caller.
    """
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

    # An owned task, waited on without cancelling it: a timeout or our own cancellation stops the child first.
    waiter = runtime.spawn(collect(), role="backup-child")
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    done: set = set()
    try:
        while not done:
            remaining = None if deadline is None else max(deadline - loop.time(), 0)
            step = (
                remaining
                if watch is None
                else min(WATCH_SECONDS, remaining if remaining is not None else WATCH_SECONDS)
            )
            done, _ = await asyncio.wait({waiter}, timeout=step)
            if done or (deadline is not None and loop.time() >= deadline):
                break
            if watch is not None:
                try:
                    watch()
                except ResourceLimit:
                    await _stop(proc, waiter, stop_grace, kill_wait)
                    raise
    except asyncio.CancelledError:
        await _stop(proc, waiter, stop_grace, kill_wait)
        raise
    if not done:
        await _stop(proc, waiter, stop_grace, kill_wait)
        return ChildResult(proc.returncode, classify_stderr(bytes(tail)), timed_out=True)
    waiter.result()
    return ChildResult(proc.returncode, classify_stderr(bytes(tail)))


async def finish_before_cleanup(work: Coroutine[Any, Any, T]) -> T:
    """Await ``work``; if cancelled meanwhile, wait for it to finish first, then propagate the cancellation."""
    task = runtime.spawn(work, role="backup-storage-io")
    try:
        await asyncio.wait({task})
        return task.result()
    except asyncio.CancelledError:
        await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # retrieved, so an error after cancellation is not reported as unhandled
        raise


class _CappedWriter:
    """A write-only file that refuses bytes past ``max_bytes`` or once stopped (from any thread)."""

    def __init__(self, path: Path, max_bytes: int, space_check: Callable[[int], None] | None = None):
        self.name = str(path)
        self._space_check = space_check
        self._next_check = 0
        self._file = open(path, "wb")  # the workspace created it 0600; truncating keeps the mode
        self._max = max_bytes
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self.written = 0
        self.refused: str | None = None

    def stop(self, category: str) -> None:
        with self._lock:
            self.refused = self.refused or category
        self._stopped.set()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        view = memoryview(data)
        with self._lock:
            if self._stopped.is_set():
                raise ResourceLimit(self.refused or "download_timeout")
            if self.written + len(view) > self._max:
                self.refused = "dump_too_large"
                self._stopped.set()
                raise ResourceLimit("dump_too_large")
            if self._space_check is not None and self.written + len(view) > self._next_check:
                try:
                    self._space_check(self.written)
                except ResourceLimit as exc:
                    self.refused = exc.category
                    self._stopped.set()
                    raise
                self._next_check = self.written + SPACE_CHECK_BYTES
            self._file.write(view)
            self.written += len(view)
            return len(view)

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


async def bounded_download(
    adapter,
    uri: str,
    path: Path,
    *,
    max_bytes: int,
    timeout: float,
    space_check: Callable[[int], None] | None = None,
) -> int:
    """Download ``uri`` into ``path`` (an existing private file); the bytes written, or ``ResourceLimit``.

    ``space_check(bytes_written)`` runs before the first write and every SPACE_CHECK_BYTES after; it raises
    ``ResourceLimit`` to stop the download while the disk still has room.
    """
    writer = _CappedWriter(Path(path), max_bytes, space_check)
    work = runtime.spawn(adapter.download_to_file(uri, writer), role="backup-download")

    async def settle(category: str) -> None:
        # After stop() returns no write is in progress and none can start, so the file is safe to remove even if
        # the adapter's thread has not noticed yet; give it a moment to end on its own, then cancel what remains.
        writer.stop(category)
        await asyncio.wait({work}, timeout=STOP_GRACE_SECONDS)
        if not work.done():
            work.cancel()
            await asyncio.wait({work})

    try:
        try:
            done, _ = await asyncio.wait({work}, timeout=timeout)
        except asyncio.CancelledError:
            await settle("cancelled")
            raise
        if not done:
            await settle("download_timeout")
            raise ResourceLimit("download_timeout")
        work.result()
        if writer.refused:
            raise ResourceLimit(writer.refused)
        return writer.written
    except (ResourceLimit, asyncio.CancelledError):
        if work.done() and not work.cancelled():
            work.exception()
        raise
    except Exception:
        if writer.refused:  # an SDK may wrap the writer's refusal in its own error type
            raise ResourceLimit(writer.refused) from None
        raise
    finally:
        writer.close()


def require_scratch(path: Path | str, needed: int, *, floor_min: int, floor_percent: int) -> None:
    """Refuse unless ``needed`` more bytes still leave the filesystem above its floor."""
    usage = shutil.disk_usage(path)
    floor = max(floor_min, usage.total * floor_percent // 100)
    if usage.free - needed < floor:
        raise ResourceLimit("scratch_space")
