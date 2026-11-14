"""Writing one export archive to private scratch, and owning it until its consumer is done.

``ArchivePlan`` lists every member (generated text and catalogue files) with its validated name,
built in the request from the captured selection. ``ExportJob`` builds the plan into one
disk-backed ZIP: each source is downloaded through the storage adapter's file API into a bounded
staging file, copied into the archive with bounded buffers, and deleted before the next. Nothing
is held as a whole archive in memory, and no session or ORM object reaches a worker thread.

Ownership. A job owns its scratch directory and the global slot lease from admission until
``release``. The build runs as a task that is never cancelled mid-write: abandoning it (a
disconnect, a deadline, shutdown) cancels its budget, so its next write raises and the worker
settles promptly. The waiting side gives it ``SETTLE_SECONDS``; a worker still running then keeps
the files and the slot, and releases them itself when it finishes. Scratch is never unlinked under
a running worker.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app import runtime
from app.adapters.models import StorageObjectNotFound
from app.exceptions import (
    DomainError,
    ExportContentUnavailableError,
    ExportLimitError,
    ExportTimeoutError,
    StorageUnavailableError,
)
from app.services.data_export import naming, resources
from app.services.data_export.resources import BoundedFile, ExportCancelled, ScratchBudget, SlotLease
from app.services.data_export.selection import SelectedFile

logger = logging.getLogger("bioaf.export")

_COPY_CHUNK = 1024 * 1024
ARCHIVE_NAME = "export.zip"


@dataclass(frozen=True)
class GeneratedMember:
    name: str
    content: bytes


@dataclass(frozen=True)
class SourceMember:
    name: str
    file: SelectedFile


Member = GeneratedMember | SourceMember


def _unavailable(file: SelectedFile) -> ExportContentUnavailableError:
    return ExportContentUnavailableError(
        f"The stored contents of {file.filename!r} are missing or unreadable, so the export was stopped. "
        "Remove or re-upload that file, then export again.",
        details={"file_uuid": str(file.uuid)},
    )


def check_available(files) -> None:
    """Refuse, before admission, a selection holding a file whose stored bytes are known gone."""
    for file in files:
        if file.storage_deleted or not file.storage_uri:
            raise _unavailable(file)


class ArchivePlan:
    """The archive's members, each name claimed once, within the entry and generated-size limits."""

    def __init__(self) -> None:
        self.members: list[Member] = []
        self._names = naming.MemberNames()
        self.generated_bytes = 0

    def _claim(self, name: str) -> str:
        if len(self.members) >= resources.MAX_ENTRIES:
            raise ExportLimitError(
                f"This export has more than {resources.MAX_ENTRIES:,} entries, the most one export can hold. "
                "Export one experiment at a time instead of the whole project.",
                details={"limit_entries": resources.MAX_ENTRIES},
            )
        return self._names.claim(name)

    def add_generated(self, name: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.generated_bytes += len(data)
        if self.generated_bytes > resources.GENERATED_ALLOWANCE:
            raise ExportLimitError(
                "This export's reports and manifests are too large to include. "
                "Export without the provenance report, or export one experiment at a time.",
            )
        self.members.append(GeneratedMember(self._claim(name), data))

    @property
    def generated_remaining(self) -> int:
        return max(resources.GENERATED_ALLOWANCE - self.generated_bytes, 0)

    def add_file(self, root: str, file: SelectedFile) -> None:
        if file.storage_deleted or not file.storage_uri:
            raise _unavailable(file)
        self.members.append(SourceMember(self._claim(f"{root}/{file.member}"), file))


def _copy_into_archive(archive: zipfile.ZipFile, name: str, source: Path | None, content: bytes | None) -> None:
    with archive.open(name, "w", force_zip64=True) as entry:
        if content is not None:
            entry.write(content)
            return
        with open(source, "rb") as f:  # type: ignore[arg-type]
            while chunk := f.read(_COPY_CHUNK):
                entry.write(chunk)


class CancellableReader:
    """A read-only view of the archive that stops an upload at its next read once the job is abandoned."""

    def __init__(self, raw, budget: ScratchBudget):
        self._raw = raw
        self._budget = budget

    def read(self, size: int = -1) -> bytes:
        if self._budget.cancelled:
            raise ExportCancelled()
        return self._raw.read(size)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._raw.seek(offset, whence)

    def tell(self) -> int:
        return self._raw.tell()


class ExportJob:
    """One admitted export: its slot lease, budget, scratch directory and its current worker task."""

    def __init__(self, lease: SlotLease, budget: ScratchBudget, workdir: Path):
        self._lease = lease
        self.budget = budget
        self.workdir = workdir
        self.archive_path = workdir / ARCHIVE_NAME
        self._task: asyncio.Task | None = None
        self._abandoned = False
        self._worker_done = False
        self._released = False

    @classmethod
    def admit(cls, sizes: list[int | None], *, scratch_dir: str) -> ExportJob:
        """Take the global slot and reserve scratch for these source sizes, or refuse."""
        reservation = resources.reservation_for(sizes)
        lease = resources.SLOT.try_acquire()
        try:
            root = Path(scratch_dir)
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            _remove_stale(root)
            budget = ScratchBudget(reservation, probe=resources.disk_probe(str(root)))
            budget.admit()
            workdir = Path(tempfile.mkdtemp(prefix="export-", dir=root))
        except BaseException:
            lease.release()
            raise
        return cls(lease, budget, workdir)

    # -- building ---------------------------------------------------------------------------------

    async def _build(self, plan: ArchivePlan, adapter) -> int:
        target = BoundedFile(self.archive_path, self.budget)
        archive = zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
        source_bytes = 0
        try:
            for index, member in enumerate(plan.members):
                if isinstance(member, GeneratedMember):
                    await asyncio.to_thread(_copy_into_archive, archive, member.name, None, member.content)
                    continue
                staging = BoundedFile(
                    self.workdir / f"source-{index}",
                    self.budget,
                    max_extent=resources.MAX_SOURCE_BYTES - source_bytes,
                )
                try:
                    await self._download(adapter, member.file, staging)
                    source_bytes += staging.extent
                    staging.close()
                    await asyncio.to_thread(_copy_into_archive, archive, member.name, staging.path, None)
                finally:
                    staging.discard()
            await asyncio.to_thread(archive.close)
        finally:
            if not target.closed:
                archive.fp = None  # a failed build: do not write a central directory
                target.close()
        if target.extent > resources.MAX_ARCHIVE_BYTES:
            raise ExportLimitError("The finished archive is larger than an export can be. Export a smaller scope.")
        return target.extent

    async def _download(self, adapter, file: SelectedFile, staging: BoundedFile) -> None:
        try:
            await adapter.download_to_file(file.storage_uri, staging)
        except (DomainError, ExportCancelled):
            raise
        except StorageObjectNotFound as exc:
            raise _unavailable(file) from exc
        except Exception as exc:
            if isinstance(exc.__cause__, (DomainError, ExportCancelled)):
                raise exc.__cause__ from None
            logger.warning("Export read failed for file %s (%s)", file.uuid, type(exc).__name__)
            raise StorageUnavailableError(
                f"The stored contents of {file.filename!r} could not be read right now. Try again later."
            ) from exc

    async def _run(self, work, undo) -> tuple[object, Exception | None]:
        # An expected failure is returned, not raised: the task owner logs raised errors as crashes.
        succeeded = False
        try:
            result = await work()
            succeeded = True
            return result, None
        except Exception as exc:
            self.budget.cancel()
            return None, exc
        except BaseException:
            self.budget.cancel()
            raise
        finally:
            if self._abandoned and succeeded and undo is not None:
                await undo()  # nobody will hand this result over: retract what it published
            self._worker_done = True
            if self._abandoned:
                self.release()

    async def _owned(self, work, *, role: str, deadline: float, timeout_message: str, undo=None):
        """Run ``work`` as the job's one owned worker and wait for it, within ``deadline``.

        Any failure, timeout or cancellation abandons the job: its worker settles (or keeps the
        scratch and slot until it does, then retracts its result with ``undo``) and the job is
        released. The caller never releases scratch under a running worker.
        """
        owner = runtime.current_owner()
        try:
            stop_watch = _watch_shutdown(owner, self.budget)
            coro = self._run(work, undo)
            self._worker_done = False
            # Not cancellable: abandoning cancels the budget instead, so workers settle before cleanup.
            if owner is not None:
                self._task = owner.spawn(coro, role=role, cancellable=False)
            else:
                self._task = runtime.spawn(coro, role=role)
        except BaseException:
            self.release()  # nothing started (the owner is shutting down)
            raise
        try:
            # ``wait`` never cancels the task: neither the deadline nor the caller's cancellation.
            done, _ = await asyncio.wait({self._task}, timeout=deadline)
            if not done:
                raise TimeoutError
            result, failure = self._task.result()
        except TimeoutError:
            await runtime.shielded(self.abandon(), role="data-export-cleanup")
            raise ExportTimeoutError(timeout_message) from None
        except BaseException:
            await runtime.shielded(self.abandon(), role="data-export-cleanup")
            raise
        finally:
            stop_watch()
        if failure is not None:
            await self.abandon()
            raise failure
        return result

    async def build(self, plan: ArchivePlan, adapter, *, deadline: float = resources.BUILD_DEADLINE) -> int:
        """Build the archive and return its size; any failure releases the job before raising."""
        return await self._owned(
            lambda: self._build(plan, adapter),
            role="data-export",
            deadline=deadline,
            timeout_message="The export took too long to prepare and was stopped. Try a smaller export.",
        )

    async def upload(self, adapter, uri: str, *, deadline: float, undo) -> None:
        """Upload the archive to ``uri``; a failed or abandoned upload's object is removed by ``undo``.

        The archive is read through the job's budget, so abandoning stops the SDK at its next read.
        """

        async def work() -> None:
            try:
                with open(self.archive_path, "rb") as raw:
                    await adapter.upload_file(uri, CancellableReader(raw, self.budget), content_type="application/zip")
            except BaseException:
                await undo()
                raise

        await self._owned(
            work,
            role="data-export-upload",
            deadline=deadline,
            timeout_message="Uploading the export for a download link took too long. Try again later.",
            undo=undo,
        )

    # -- ownership --------------------------------------------------------------------------------

    @property
    def worker_running(self) -> bool:
        return self._task is not None and not self._worker_done

    async def abandon(self) -> None:
        """Stop the build; release now if its worker settles in time, else when it finishes."""
        self.budget.cancel()
        self._abandoned = True
        if self.worker_running:
            done, _ = await asyncio.wait({self._task}, timeout=resources.SETTLE_SECONDS)  # type: ignore[arg-type]
            if not done:
                logger.warning("Export worker still running after cancellation; it will release on exit")
                return
        self.release()

    def release(self) -> None:
        """Delete scratch and return the slot. Never while the worker runs; idempotent."""
        if self.worker_running:
            self._abandoned = True
            return
        if self._released:
            return
        self._released = True
        try:
            shutil.rmtree(self.workdir, ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.error("Export scratch cleanup failed (%s)", type(exc).__name__)
        finally:
            self._lease.release()


def _remove_stale(root: Path) -> None:
    """Holding the slot, any scratch left in the root belongs to no live export (a hard kill)."""
    for entry in root.iterdir():
        if entry.name.startswith("export-"):
            shutil.rmtree(entry, ignore_errors=True)


def _watch_shutdown(owner, budget: ScratchBudget) -> Callable[[], None]:
    """Cancel the budget when the application starts stopping; returns the watcher's stop."""
    if owner is None:
        return lambda: None

    async def watch() -> None:
        await owner.stopping.wait()
        budget.cancel()

    watcher = owner.spawn(watch(), role="data-export-shutdown-watch")
    return watcher.cancel
