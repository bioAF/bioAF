"""Bounds on what one data export may use: source size, scratch disk, time, and the one global slot.

All values are the owner-accepted provisional envelope (plan 24 decisions 4, 5 and 8).

Scratch reservation. For the selected files, with ``S`` their known total size, ``L`` the largest,
``G`` the generated-content allowance and ``H = max(32 MiB, ceil((S + G) / 20))`` the ZIP overhead
allowance, an export reserves ``R = S + max(L, G) + G + H``: the archive (sources, generated entries,
overhead) plus one staging file at a time. Any unknown or invalid size reserves the whole ceiling
``C``; a computed ``R`` above ``C`` is refused, never clamped. Admission needs
``free - R >= max(3 GiB, 10% of the filesystem)``.

Metadata is not trusted capacity. Every scratch write goes through a ``BoundedFile`` charging a
shared ``ScratchBudget`` by positive high-water growth, before the bytes reach disk, so a retried or
out-of-order range is charged once and an understated size fails instead of eating the floor. A
discarded staging file returns its extent. While growing, the budget re-checks
``free - (R - charged) >= floor``: reserved-but-unwritten bytes must still fit above the floor, and
bytes already written are not counted twice.

The disk is shared with Docker and PostgreSQL. The floor is an export refusal rule; it does not
stop other writers taking space between checks.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import ExportBusyError, ExportCapacityError, ExportLimitError

MiB = 1024 * 1024
GiB = 1024 * MiB

MAX_SOURCE_BYTES = 2 * GiB
MAX_FILES = 10_000
MAX_ENTRIES = 20_000
GENERATED_ALLOWANCE = 64 * MiB
MAX_ARCHIVE_BYTES = 2304 * MiB
SCRATCH_CEILING = 5 * GiB
FLOOR_MINIMUM = 3 * GiB
FLOOR_FRACTION = 10
MIN_ZIP_OVERHEAD = 32 * MiB

# Re-measure free space after this much unchecked growth (and at each new file).
CHECK_INTERVAL = 8 * MiB

# Deadlines, seconds.
BUILD_DEADLINE = 30 * 60
ISSUE_DEADLINE = 15 * 60
TRANSFER_DEADLINE = 30 * 60
SETTLE_SECONDS = 10


class ExportCancelled(Exception):
    """The export was abandoned (disconnect, deadline, failure); its worker stops at the next write."""


def _gib(n: int) -> str:
    return f"{n / GiB:g} GiB"


def check_source_limits(sizes: list[int | None], *, fastq_omitted: bool) -> None:
    """Refuse a selection over the file count or known source-size ceiling, before any work."""
    if len(sizes) > MAX_FILES:
        raise ExportLimitError(
            f"This export has more than {MAX_FILES:,} files, the most one export can hold. "
            "Export one experiment at a time instead of the whole project.",
            details={"limit_files": MAX_FILES},
        )
    known = sum(s for s in sizes if isinstance(s, int) and s > 0)
    if known > MAX_SOURCE_BYTES:
        advice = (
            "Export one experiment at a time instead of the whole project."
            if fastq_omitted
            else "Leave out the FASTQ files, or export one experiment at a time instead of the whole project."
        )
        raise ExportLimitError(
            f"This export is larger than {_gib(MAX_SOURCE_BYTES)}, the most one export can hold. {advice}",
            details={"limit_bytes": MAX_SOURCE_BYTES},
        )


def reservation_for(sizes: Iterable[int | None]) -> int:
    """Scratch bytes to reserve for these source sizes (see the module docstring)."""
    sizes = list(sizes)
    if any(not isinstance(s, int) or isinstance(s, bool) or s < 0 for s in sizes):
        return SCRATCH_CEILING
    total = sum(sizes)
    largest = max(sizes, default=0)
    overhead = max(MIN_ZIP_OVERHEAD, -(-(total + GENERATED_ALLOWANCE) // 20))
    reservation = total + max(largest, GENERATED_ALLOWANCE) + GENERATED_ALLOWANCE + overhead
    if reservation > SCRATCH_CEILING:
        raise ExportLimitError(
            f"This export needs more than {_gib(SCRATCH_CEILING)} of working space to build. "
            "Export one experiment at a time instead of the whole project.",
            details={"limit_bytes": MAX_SOURCE_BYTES},
        )
    return reservation


@dataclass(frozen=True)
class DiskSpace:
    free: int
    total: int


def floor_for(total: int) -> int:
    return max(FLOOR_MINIMUM, total // FLOOR_FRACTION)


def admits(disk, reservation: int) -> bool:
    return disk.free - reservation >= floor_for(disk.total)


def disk_probe(path: str) -> Callable[[], DiskSpace]:
    def probe() -> DiskSpace:
        usage = shutil.disk_usage(path)
        return DiskSpace(free=usage.free, total=usage.total)

    return probe


class ScratchBudget:
    """One export's reserved scratch bytes, charged by bounded files from any thread."""

    def __init__(self, reservation: int, *, probe: Callable[[], DiskSpace]):
        self.reservation = reservation
        self._probe = probe
        self._lock = threading.Lock()
        self._charged = 0
        self._unchecked = 0
        self._cancelled = threading.Event()

    @property
    def charged(self) -> int:
        return self._charged

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _measure(self):
        try:
            return self._probe()
        except OSError as exc:
            raise ExportCapacityError(
                "The server could not measure its free disk space, so the export was stopped."
            ) from exc

    def _check_locked(self) -> None:
        disk = self._measure()
        if disk.free - (self.reservation - self._charged) < floor_for(disk.total):
            raise ExportCapacityError("The server is running low on disk space, so the export was stopped.")
        self._unchecked = 0

    def check(self) -> None:
        """Fail unless reserved-but-unwritten bytes still fit above the floor."""
        with self._lock:
            self._check_locked()

    def admit(self) -> None:
        """The admission check: the whole reservation must fit above the floor."""
        if not admits(self._measure(), self.reservation):
            raise ExportCapacityError(
                "The server does not have enough free disk space to build this export right now. "
                "Try a smaller export, or try again later.",
            )

    def charge(self, growth: int) -> None:
        if self._cancelled.is_set():
            raise ExportCancelled()
        if growth <= 0:
            return
        with self._lock:
            if self._charged + growth > self.reservation:
                raise ExportCapacityError(
                    "The export grew past the space reserved for it (a file is larger than the catalogue "
                    "records), so it was stopped."
                )
            self._unchecked += growth
            if self._unchecked >= CHECK_INTERVAL:
                self._check_locked()
            self._charged += growth

    def refund(self, extent: int) -> None:
        with self._lock:
            self._charged = max(0, self._charged - extent)


class BoundedFile:
    """A seekable, write-only scratch file whose growth is charged to a ``ScratchBudget`` first.

    Extent is the high-water mark ``max(previous, offset + length)``; only positive growth is
    charged, so rewriting a range (an SDK retry) costs nothing and a write past the end charges the
    hole too. Growth the budget refuses never reaches the disk. ``discard`` deletes the file and
    returns its extent. Writes from the SDK's I/O thread and the archive worker are serialized
    per file by its lock; the budget is shared and thread-safe.
    """

    def __init__(self, path: str | os.PathLike, budget: ScratchBudget):
        self.path = Path(path)
        self._budget = budget
        self._lock = threading.Lock()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        self._file = os.fdopen(fd, "w+b", buffering=0)
        self._extent = 0
        self._discarded = False
        budget.check()

    @property
    def extent(self) -> int:
        return self._extent

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        with self._lock:
            return self._file.seek(offset, whence)

    def tell(self) -> int:
        with self._lock:
            return self._file.tell()

    def write(self, data) -> int:
        view = memoryview(data)
        with self._lock:
            end = self._file.tell() + len(view)
            self._budget.charge(end - self._extent)
            self._extent = max(self._extent, end)
            written = 0
            while written < len(view):
                written += self._file.write(view[written:])
            return written

    def truncate(self, size: int | None = None) -> int:
        with self._lock:
            size = self._file.tell() if size is None else size
            self._budget.charge(size - self._extent)
            result = self._file.truncate(size)
            if size < self._extent:
                self._budget.refund(self._extent - size)
            self._extent = size
            return result

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def discard(self) -> None:
        """Close and delete the file, returning its extent to the budget."""
        if self._discarded:
            return
        self._discarded = True
        self._file.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._budget.refund(self._extent)
        self._extent = 0

    def __enter__(self) -> BoundedFile:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class SlotLease:
    def __init__(self, release: Callable[[], None]):
        self._release = release
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release()


class ExportSlot:
    """One active data export per serving process, across users, organizations and delivery modes.

    The image runs one serving process (app/runtime.py), so this is the deployment's only slot.
    There is no queue and no fairness between tenants: a busy request is refused and may retry.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def try_acquire(self) -> SlotLease:
        if not self._lock.acquire(blocking=False):
            raise ExportBusyError("Another data export is being prepared. Try again in a few minutes.")
        return SlotLease(self._lock.release)

    @property
    def busy(self) -> bool:
        return self._lock.locked()


SLOT = ExportSlot()
