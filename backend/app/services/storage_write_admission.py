"""Write admission for the installation's storage (approved D5, mockup M11).

Before storage destroy deletes anything it closes admission: new uploads are refused before any byte
reaches storage and before any signed URL is issued. Uploads already running get up to 30 seconds to
finish; if any are still running, or bioAF cannot tell (a signed URL was issued and never reported
back), destroy stops before deleting anything and uploads stay paused until an administrator allows
them again or runs Destroy Storage again. Running uploads are never cut off, and unfinished uploads
are kept.

A direct upload settles only when the provider confirms it (completion, or a cancellation the
provider acknowledges), or when an administrator releases one bioAF lost track of; see
``upload_settlement``. A presigned URL's expiry is not proof its upload finished: a transfer that
started before expiry can still land.

The paused state is durable (platform_config ``storage_writes_paused``) so a restart keeps it.
Proxied transfers in flight are tracked in this process: the supported deployment has one serving
process.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.platform.platform_config_service import PlatformConfigService

logger = logging.getLogger("bioaf.storage_write_admission")

PAUSED_KEY = "storage_writes_paused"
DRAIN_SECONDS = 30.0
UPLOADS_PAUSED = (
    "Uploads are paused. An administrator is changing storage. Your files have not been sent. Try again later."
)

# Proxied uploads streaming through this process: checkpoint ID -> start time.
_in_flight: dict[str, datetime] = {}

# The writer claim the current task holds, recorded on each storage call it makes. A call that
# outlives its claim (the awaiting task was cancelled) still counts as a writer until its thread stops.
_claim: ContextVar[str | None] = ContextVar("bioaf_storage_writer_claim", default=None)


def _hold(key: str):
    _in_flight[key] = datetime.now(timezone.utc)
    return _claim.set(key)


def _drop(key: str, token) -> None:
    _in_flight.pop(key, None)
    try:
        _claim.reset(token)
    except ValueError:
        # Closed from another context: the variable dies with that context.
        pass


def _install() -> None:
    from app.adapters.storage import workers

    workers.set_claim_lookup(_claim.get)


_install()


UPLOAD_STORAGE_UNAVAILABLE = (
    "Uploads are unavailable because bioAF cannot reach its upload storage. Your files have not been sent. "
    "Ask an administrator to check Storage under Infrastructure > Components."
)


class UploadsPausedError(ConflictError):
    """New uploads are refused while storage is being changed."""

    code = "uploads_paused"


STORAGE_PAUSED = "An administrator has paused uploads and file saving. Try again later, or contact your administrator."


class StorageWritesPaused(ConflictError):
    """A background write to installation storage is refused while storage is being changed."""

    code = "storage_writes_paused"


class UploadStorageUnavailableError(ConflictError):
    """The store an upload would write to is known to be unusable, so nothing is sent."""

    code = "upload_storage_unavailable"


@dataclass(frozen=True)
class OutstandingWriter:
    upload_id: str
    user_id: int | None
    experiment_id: int | None
    filename: str
    started_at: datetime
    kind: str
    # "sending" (bytes may be arriving), "stopping" (cancelled, the provider has not confirmed
    # yet), or "unconfirmed" (bioAF lost track; an administrator may release it).
    state: str = "sending"
    reference: str | None = None
    settles_after: datetime | None = None

    @property
    def needs_release(self) -> bool:
        return self.state == "unconfirmed"


async def paused(session: AsyncSession) -> dict | None:
    value = await PlatformConfigService.get(session, PAUSED_KEY)
    if not value or value == "null":
        return None
    try:
        return json.loads(value)
    except ValueError:
        return {"since": None}


async def require_writes_open(session: AsyncSession) -> None:
    """Refuse a new upload while storage writes are paused, before any byte is stored."""
    if await paused(session) is not None:
        raise UploadsPausedError(UPLOADS_PAUSED)


def _set_mirror(value: bool) -> None:
    global _paused_mirror
    _paused_mirror = value


async def require_store_usable(session: AsyncSession, store: str) -> None:
    """Refuse an upload before anything is written or signed when its store is known unusable.

    Only definite answers refuse (missing, refused, another installation's, not mounted). A store
    that is not confirmed, cannot be checked or did not answer keeps accepting uploads: the
    transfer itself then reports any failure.
    """
    from app.services import storage_identity
    from app.services.storage_identity import StoreHealth

    unusable = {StoreHealth.NOT_FOUND, StoreHealth.NO_ACCESS, StoreHealth.WRONG_OWNER, StoreHealth.NOT_MOUNTED}
    if getattr(storage_identity._adapter(), "is_local", False) is True:
        # Local (simulated) storage creates a store's directory on first write.
        return
    bucket = (await storage_identity._configured_buckets(session)).get(store)
    if not bucket:
        return
    try:
        report = await storage_identity.inspect_bucket(session, store, bucket)
    except Exception as exc:
        logger.warning("Could not check the %s store before an upload (error_type=%s)", store, type(exc).__name__)
        return
    if report.health in unusable:
        logger.warning("Upload refused: the %s store is %s", store, report.health.value)
        raise UploadStorageUnavailableError(UPLOAD_STORAGE_UNAVAILABLE)


async def pause_writes(session: AsyncSession, operation_id: int | None) -> None:
    """Close admission. Committed by the caller before waiting for running uploads."""
    if await paused(session) is None:
        await PlatformConfigService.set(
            session,
            PAUSED_KEY,
            json.dumps({"operation_id": operation_id, "since": datetime.now(timezone.utc).isoformat()}),
        )
        logger.info("Storage writes paused for operation %s", operation_id)
    _set_mirror(True)


async def allow_writes(session: AsyncSession, user_id: int | None = None) -> bool:
    """Reopen uploads. Returns whether they were paused. The caller commits."""
    from app.services.audit_service import log_action

    if await paused(session) is None:
        _set_mirror(False)
        return False
    await PlatformConfigService.set(session, PAUSED_KEY, None)
    _set_mirror(False)
    if user_id is not None:
        await log_action(
            session,
            user_id=user_id,
            entity_type="infrastructure",
            entity_id=0,
            action="storage_writes_allowed",
            details={},
        )
    logger.info("Storage writes reopened")
    return True


@asynccontextmanager
async def tracked_transfer(upload_id: str) -> AsyncIterator[None]:
    """Mark a proxied transfer as running in this process for the duration of the block."""
    token = _hold(upload_id)
    try:
        yield
    finally:
        _drop(upload_id, token)


def admits_writes(fn):
    """Run an async function whose first argument is the request session as one admitted writer."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        session = kwargs["session"] if "session" in kwargs else args[0]
        async with admitted(session):
            return await fn(*args, **kwargs)

    return wrapper


_BACKGROUND = "background:"
_ADMITTED = "admitted:"


@asynccontextmanager
async def admitted(
    session: AsyncSession, *, error: type[ConflictError] | None = None, message: str | None = None
) -> AsyncIterator[None]:
    """Admit one request that may write to storage (an upload, a filing step).

    The request is registered as a writer before the pause is read, so a destroy that pauses writes
    either sees the pause refuse it or finds it registered and waits for it: nothing can slip in
    between the pause and the drain. Raises ``error`` (UploadsPausedError by default) while paused.
    """
    import uuid

    key = f"{_ADMITTED}{uuid.uuid4()}"
    token = _hold(key)
    try:
        if await paused(session) is not None:
            raise (error or UploadsPausedError)(message or UPLOADS_PAUSED)
        yield
    finally:
        _drop(key, token)


# This process's view of ``storage_writes_paused``, so a background write needs no session of
# its own. None until read: a fresh process reads the durable setting first. Only pause_writes
# and allow_writes change the setting, both in the one serving process.
_paused_mirror: bool | None = None


async def _paused_now() -> bool:
    global _paused_mirror
    if _paused_mirror is None:
        from app import database

        async with database.async_session_factory() as session:
            _paused_mirror = await paused(session) is not None
    return _paused_mirror


@asynccontextmanager
async def background_write(label: str) -> AsyncIterator[None]:
    """Admit one background write to installation storage (thumbnails, QC caches, exports,
    build contexts, reference imports).

    Registered before the pause is checked, so a destroy that pauses writes either refuses this
    write or waits for it. Raises StorageWritesPaused before anything is written while paused.
    """
    import uuid

    key = f"{_BACKGROUND}{label}:{uuid.uuid4()}"
    token = _hold(key)
    try:
        if await _paused_now():
            logger.info("Background %s write skipped: storage writes are paused", label)
            raise StorageWritesPaused(STORAGE_PAUSED)
        yield
    finally:
        _drop(key, token)


async def write_in_thread(label: str, work):
    """Run blocking storage-writing ``work`` in a thread as an admitted background write.

    A thread cannot be interrupted: cancelling the awaiting task leaves it writing. The write stays
    registered until the thread itself returns, so destroy never takes a cancelled awaiter for a
    finished writer. Raises StorageWritesPaused before the thread starts while writes are paused.
    """
    import uuid

    from app import runtime

    key = f"{_BACKGROUND}{label}:{uuid.uuid4()}"
    _in_flight[key] = datetime.now(timezone.utc)
    try:
        if await _paused_now():
            logger.info("Background %s write skipped: storage writes are paused", label)
            raise StorageWritesPaused(STORAGE_PAUSED)
    except BaseException:
        _in_flight.pop(key, None)
        raise

    async def tracked():
        try:
            return await asyncio.to_thread(work)
        finally:
            _in_flight.pop(key, None)

    # Owned and not cancellable: cancelling the caller stops the wait, not the thread's work.
    return await runtime.shielded(tracked(), role="storage-background-write")


async def outstanding_writers(session: AsyncSession) -> list[OutstandingWriter]:
    """Uploads that may still write: transfers in flight and signed URLs never reported back."""
    from app.models.upload_checkpoint import UploadCheckpoint

    from app.services.upload_settlement import needs_release, settles_after

    rows = (
        await session.execute(
            select(UploadCheckpoint).where(UploadCheckpoint.stage.in_(("issued", "transferring", "cancelling")))
        )
    ).scalars()
    writers = [
        OutstandingWriter(
            r.id,
            r.user_id,
            r.experiment_id,
            r.filename,
            r.created_at,
            r.kind,
            state="unconfirmed" if needs_release(r) else ("stopping" if r.stage == "cancelling" else "sending"),
            reference=r.reference,
            settles_after=settles_after(r),
        )
        for r in rows
        if r.stage != "transferring" or r.id in _in_flight
    ]
    # A proxied transfer this process is running but whose checkpoint is not visible yet.
    known = {w.upload_id for w in writers}
    for upload_id, started in list(_in_flight.items()):
        if upload_id in known:
            continue
        if upload_id.startswith(_BACKGROUND):
            label = upload_id[len(_BACKGROUND) :].rsplit(":", 1)[0]
            writers.append(OutstandingWriter(upload_id, None, None, label, started, "background"))
        else:
            writers.append(OutstandingWriter(upload_id, None, None, "", started, "proxied"))
    # Storage calls still running after the request or task that made them stopped waiting: a
    # cancelled upload or background write keeps changing storage until its thread returns.
    from app.adapters.storage import workers

    for n, worker in enumerate(workers.running()):
        if worker.operation_id is None and worker.claim not in _in_flight:
            writers.append(OutstandingWriter(f"worker:{n}", None, None, worker.label, worker.started_at, "background"))
    return writers


async def drain(session_factory, seconds: float = DRAIN_SECONDS, interval: float = 1.0) -> list[OutstandingWriter]:
    """Wait up to ``seconds`` for running uploads to settle; return the ones that did not."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        async with session_factory() as session:
            remaining = await outstanding_writers(session)
        if not remaining or loop.time() >= deadline:
            return remaining
        await asyncio.sleep(min(interval, max(0.0, deadline - loop.time())))
