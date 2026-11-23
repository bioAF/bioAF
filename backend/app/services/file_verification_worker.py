"""The owned background worker that verifies file checksums (01B.4).

One attempt is three steps, and no database transaction or row lock is held across storage I/O:

1. **Claim** (short transaction, ``FOR UPDATE SKIP LOCKED``): take one due record (queued or waiting
   for its expectation, or a verification whose lease expired after a crash), give it a fresh lease
   token and expiry, count the attempt, and commit.
2. **Work** (no transaction): resolve a pending expectation, or obtain the digest of the file's
   current object: a trustworthy native digest, else an ordered streamed read. On NFS the worker
   first makes a protected managed copy (app/adapters/storage/nfs_managed_copy.py) and hashes that.
   Progress and the lease are renewed in tiny guarded updates; a lost lease stops the attempt.
3. **Complete** (short transaction): lock the record and the file, and apply the result only if the
   lease is still ours, the expectation revision is unchanged, and the file is live at the URI that
   was read. A match records the proof, installs an NFS artifact as the canonical URI, and calls
   ``FileService.mark_ready`` (the only producer of ``file.ready``); proof, readiness and the deferred
   event commit together. A mismatch keeps both values and is not retried automatically.

Transient failures retry five times in all, 60/300/1800/7200 seconds apart, then the record is
``failed`` (an authorized retry can requeue it). Permission and configuration problems fail at once
with ``retryable=False``. Failures that need a person raise a ``file_verification_failed`` notification
for the file's responsible user. Lease recovery after a restart keeps the attempt count and restarts
a read from byte zero.

Startup and the live writers that queue work are activated together in 01C.2.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update

from app import runtime
from app.adapters.models import ObjectIdentity, ObjectChanged, ObjectDigest, StorageObjectNotFound
from app.exceptions import ValidationError
from app.models.file import File
from app.models.file_verification import FileVerification
from app.services import file_verification_service as fvs
from app.services.file_service import FileService

logger = logging.getLogger("bioaf.file_verification_worker")

POLL_INTERVAL_SECONDS = 15
RETRY_DELAYS_SECONDS = (60, 300, 1800, 7200)
MAX_ATTEMPTS = len(RETRY_DELAYS_SECONDS) + 1
_HEARTBEAT_SECONDS = 2.0

# expectation_source -> async (session, file, discovery_context) -> expected md5, or None when the
# resolved source carries no checksum. Raising means discovery failed (retried, never "no checksum").
Resolver = Callable[[Any, File, dict], Awaitable[str | None]]
DISCOVERY_RESOLVERS: dict[str, Resolver] = {}


class LeaseLost(Exception):
    """Another worker owns this record now; stop without writing."""


@dataclass(frozen=True)
class Claim:
    record_id: int
    file_id: int
    org_id: int
    file_uuid: uuid.UUID
    token: uuid.UUID
    revision: int
    discovery: bool
    source: str | None
    context: dict
    uri: str
    attempt: int
    prior_artifact_uri: str | None


@dataclass(frozen=True)
class Result:
    digest: ObjectDigest | None = None
    final_uri: str | None = None
    artifact_uuid: uuid.UUID | None = None
    discovered: tuple[bool, str | None] | None = None  # (resolved, md5)
    # The exact source version a protected copy was made from, when it was unchanged throughout the
    # copy: once the copy is installed, that object is superseded.
    superseded: ObjectIdentity | None = None
    error: str | None = None
    transient: bool = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _settings():
    from app.config import settings

    return settings


def _factory():
    from app import database as database_module

    return database_module.async_session_factory


def _producer_uids() -> tuple[int, ...]:
    raw = str(getattr(_settings(), "nfs_producer_uids", "") or "")
    return tuple(int(part) for part in raw.replace(" ", "").split(",") if part)


def _classify(exc: BaseException) -> tuple[str, bool]:
    from app.adapters.storage.nfs_managed_copy import (
        InsufficientCapacity,
        ManagedCopyRefused,
        SeparationNotEstablished,
    )

    if isinstance(exc, StorageObjectNotFound):
        return "object_missing", True
    if isinstance(exc, ObjectChanged):
        return "object_changed", True
    if isinstance(exc, SeparationNotEstablished):
        return "nfs_separation_unavailable", False
    if isinstance(exc, ManagedCopyRefused):
        return "managed_copy_refused", False
    if isinstance(exc, InsufficientCapacity):
        return "nfs_capacity_insufficient", True
    if isinstance(exc, PermissionError):
        return "storage_permission_denied", False
    if isinstance(exc, TimeoutError):
        return "verification_timeout", True
    return "storage_unavailable", True


# --- 1. claim ---------------------------------------------------------------------------------


async def claim_next() -> Claim | None:
    now = _now()
    lease = timedelta(seconds=_settings().file_verification_lease_seconds)
    async with _factory()() as session:
        due = or_(
            and_(
                FileVerification.status.in_([fvs.QUEUED, fvs.WAITING_EXPECTATION]),
                or_(FileVerification.next_attempt_at.is_(None), FileVerification.next_attempt_at <= now),
            ),
            and_(
                FileVerification.status.in_([fvs.PREPARING, fvs.VERIFYING]),
                or_(FileVerification.lease_expires_at.is_(None), FileVerification.lease_expires_at < now),
            ),
        )
        record = (
            await session.execute(
                select(FileVerification)
                .where(due)
                .order_by(FileVerification.next_attempt_at.asc().nulls_first(), FileVerification.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        file = await session.get(File, record.file_id)
        discovery = record.status == fvs.WAITING_EXPECTATION
        current_uri = file.storage_uri or file.gcs_uri
        # An artifact left by an unfinished attempt is an orphan to remove first; one that is now the
        # file's canonical bytes (a later expectation change re-verifies it) is never touched.
        prior = record.artifact_uri if record.artifact_uri and record.artifact_uri != current_uri else None
        record.lease_token = uuid.uuid4()
        record.lease_expires_at = now + lease
        record.attempt_count = (record.attempt_count or 0) + 1
        if discovery:
            record.next_attempt_at = record.lease_expires_at
        else:
            record.status = fvs.VERIFYING
            record.stage = None
            record.bytes_processed = None
        claim = Claim(
            record_id=record.id,
            file_id=record.file_id,
            org_id=record.organization_id,
            file_uuid=file.uuid,
            token=record.lease_token,
            revision=record.expectation_revision,
            discovery=discovery,
            source=record.expectation_source,
            context=dict(record.discovery_context or {}),
            uri=current_uri,
            attempt=record.attempt_count,
            prior_artifact_uri=prior,
        )
        await session.commit()
        return claim


# --- 2. work ----------------------------------------------------------------------------------


async def _guarded(claim: Claim, **values) -> None:
    """Write progress/lease fields only while the lease is still ours."""
    async with _factory()() as session:
        result = await session.execute(
            update(FileVerification)
            .where(FileVerification.id == claim.record_id, FileVerification.lease_token == claim.token)
            .values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        raise LeaseLost()


def _heartbeat(claim: Claim, stage: str, total: int):
    lease = timedelta(seconds=_settings().file_verification_lease_seconds)
    last = 0.0

    async def beat(done: int) -> None:
        nonlocal last
        moment = time.monotonic()
        if moment - last < _HEARTBEAT_SECONDS and done < total:
            return
        last = moment
        await _guarded(claim, stage=stage, bytes_processed=done, total_bytes=total, lease_expires_at=_now() + lease)

    return beat


async def _discover(claim: Claim) -> Result:
    resolver = DISCOVERY_RESOLVERS.get(claim.source or "")
    if resolver is None:
        _load_default_resolvers()
        resolver = DISCOVERY_RESOLVERS.get(claim.source or "")
    if resolver is None:
        return Result(error="expectation_discovery_failed", transient=False)
    try:
        async with _factory()() as session:
            file = await session.get(File, claim.file_id)
            md5 = await resolver(session, file, claim.context)
            await session.rollback()
    except ValidationError:
        # A malformed checksum in the source will not fix itself on retry.
        return Result(error="expectation_discovery_failed", transient=False)
    except Exception:
        logger.warning("File %s: expectation discovery failed", claim.file_id, exc_info=True)
        return Result(error="expectation_discovery_failed", transient=True)
    return Result(discovered=(True, md5))


async def _verify(claim: Claim, adapter) -> Result:
    settings = _settings()
    chunk = settings.file_verification_chunk_bytes
    if hasattr(adapter, "prepare_managed_copy"):
        store, _ = adapter.parse_uri(claim.uri)
        if claim.prior_artifact_uri:
            await adapter.remove_managed_artifact(claim.prior_artifact_uri)
        adapter.managed_preflight(store, _producer_uids())
        artifact_uuid = uuid.uuid4()
        from app.adapters.storage.nfs_managed_copy import managed_key

        artifact_uri = adapter.build_uri(store, managed_key(claim.file_uuid, artifact_uuid))
        # Ownership is recorded before any I/O, so restart cleanup can tell an orphan from work in use.
        await _guarded(
            claim, status=fvs.PREPARING, stage="preparing", artifact_uuid=artifact_uuid, artifact_uri=artifact_uri
        )
        source = await adapter.object_identity(claim.uri)
        copy = await adapter.prepare_managed_copy(
            claim.uri,
            dest_store=store,
            file_uuid=claim.file_uuid,
            artifact_uuid=artifact_uuid,
            chunk_size=chunk,
            reserve_bytes=settings.nfs_managed_copy_reserve_bytes,
            on_progress=_heartbeat(claim, "preparing", source.size_bytes),
        )
        identity = await adapter.object_identity(copy.uri)
        try:
            after = await adapter.object_identity(claim.uri)
        except Exception:
            after = None
        await _guarded(claim, status=fvs.VERIFYING, stage="verifying", bytes_processed=0)
        digest = await adapter.stream_md5(
            identity, chunk_size=chunk, on_progress=_heartbeat(claim, "verifying", identity.size_bytes)
        )
        return Result(
            digest=digest,
            final_uri=copy.uri,
            artifact_uuid=artifact_uuid,
            superseded=source if after is not None and after.version == source.version else None,
        )
    identity = await adapter.object_identity(claim.uri)
    if identity.native_md5:
        digest = ObjectDigest(
            uri=claim.uri,
            hex=identity.native_md5,
            size_bytes=identity.size_bytes,
            version=identity.version,
            method="provider",
        )
        return Result(digest=digest, final_uri=claim.uri)
    await _guarded(claim, stage="verifying", bytes_processed=0, total_bytes=identity.size_bytes)
    digest = await adapter.stream_md5(
        identity, chunk_size=chunk, on_progress=_heartbeat(claim, "verifying", identity.size_bytes)
    )
    return Result(digest=digest, final_uri=claim.uri)


# --- 3. complete ------------------------------------------------------------------------------


async def _notify(session, file: File, code: str) -> None:
    await fvs.notify_failure(session, file, code)


async def _locked(session, claim: Claim) -> tuple[FileVerification | None, File | None]:
    record = (
        await session.execute(select(FileVerification).where(FileVerification.id == claim.record_id).with_for_update())
    ).scalar_one_or_none()
    file = (await session.execute(select(File).where(File.id == claim.file_id).with_for_update())).scalar_one_or_none()
    if record is None or file is None or record.lease_token != claim.token:
        return None, None
    return record, file


def _release(record: FileVerification) -> None:
    record.lease_token = None
    record.lease_expires_at = None


async def _fail(session, record: FileVerification, file: File, code: str, transient: bool, *, discovery: bool) -> None:
    _release(record)
    record.error_code = code
    if transient and record.attempt_count < MAX_ATTEMPTS:
        record.status = fvs.WAITING_EXPECTATION if discovery else fvs.QUEUED
        record.retryable = True
        record.next_attempt_at = _now() + timedelta(seconds=RETRY_DELAYS_SECONDS[record.attempt_count - 1])
        return
    record.status = fvs.FAILED
    record.retryable = transient
    record.next_attempt_at = None
    logger.warning("File %s verification failed: %s", file.id, code)
    await _notify(session, file, code)


async def complete(claim: Claim, result: Result) -> str:
    """Apply one attempt's result. Returns ``applied``, ``stale`` (discarded), or ``lost`` (lease gone)."""
    async with _factory()() as session:
        record, file = await _locked(session, claim)
        if record is None:
            await session.rollback()
            return "lost"  # our lease was taken over; the new owner decides
        if file.deleted_at is not None or file.storage_deleted:
            _release(record)
            record.status = fvs.FAILED
            record.error_code = "file_deleted"
            record.retryable = False
            record.next_attempt_at = None
            await session.commit()
            return "stale"
        if record.expectation_revision != claim.revision or (file.storage_uri or file.gcs_uri) != claim.uri:
            # The expectation or the object changed under us: this result proves nothing now.
            _release(record)
            if record.status in (fvs.PREPARING, fvs.VERIFYING):
                record.status = fvs.QUEUED
            record.next_attempt_at = _now()
            await session.commit()
            return "stale"
        if result.error is not None:
            await _fail(session, record, file, result.error, result.transient, discovery=claim.discovery)
            await session.commit()
            return "applied"
        if result.discovered is not None:
            _release(record)
            record.error_code = None
            md5 = result.discovered[1]
            await session.flush()
            if md5 is None:
                await fvs.FileVerificationService.record_not_required(session, file, source=claim.source or "")
                await FileService.mark_ready(session, file.id, file.organization_id)
            else:
                try:
                    await fvs.FileVerificationService.record_expectation(
                        session, file, source=claim.source or "", md5=md5
                    )
                except Exception:
                    await _fail(session, record, file, "expectation_discovery_failed", False, discovery=True)
            await session.commit()
            return "applied"

        digest = result.digest
        assert digest is not None
        _release(record)
        record.observed_checksum = digest.hex
        record.observed_method = digest.method
        record.observed_bytes = digest.size_bytes
        record.observed_uri = result.final_uri
        record.observed_object_version = digest.version
        record.bytes_processed = digest.size_bytes
        record.total_bytes = digest.size_bytes
        record.stage = None
        record.next_attempt_at = None
        if digest.hex != record.expected_checksum:
            record.status = fvs.CHECKSUM_MISMATCH
            record.error_code = "checksum_mismatch"
            record.retryable = False
            logger.warning("File %s checksum does not match its expectation", file.id)
            await _notify(session, file, "checksum_mismatch")
            await session.commit()
            return "applied"
        record.status = fvs.VERIFIED
        record.verified_at = _now()
        record.error_code = None
        record.retryable = None
        superseded = None
        if result.final_uri != claim.uri:
            if file.source_type == "pipeline_output" and file.source_storage_uri is None:
                file.source_storage_uri = claim.uri
            file.storage_uri = result.final_uri
            # A pipeline's output stays its producer's (it is the file's durable source identity). Any
            # other file's previous copy is now unreferenced: remove exactly the version that was
            # copied, after this commits, through the File's one cleanup slot.
            if file.source_type != "pipeline_output" and result.superseded is not None:
                from app.services import storage_cleanup

                if file.pending_storage_cleanup:
                    await storage_cleanup.clean_pending(session, file)
                if not file.pending_storage_cleanup:
                    file.pending_storage_cleanup = storage_cleanup.cleanup_slot(result.superseded)
                    superseded = file.id, file.organization_id
                else:
                    logger.warning("File %s: an earlier cleanup is pending; the replaced copy is kept", file.id)
        await session.flush()
        await FileService.mark_ready(session, file.id, file.organization_id)
        await session.commit()
        if superseded is not None:
            from app.services import storage_cleanup

            try:
                await storage_cleanup.cleanup_after_commit(*superseded)
            except Exception as exc:  # the periodic cleanup pass retries
                logger.warning("Replaced copy cleanup deferred (error_type=%s)", type(exc).__name__)
        return "applied"


async def _discard_artifact(adapter, result: Result) -> None:
    if result.artifact_uuid is None or result.final_uri is None:
        return
    try:
        await adapter.remove_managed_artifact(result.final_uri)
    except Exception:
        logger.warning("Could not remove abandoned verification artifact %s", result.artifact_uuid, exc_info=True)


async def run_once() -> bool:
    """Claim and process one due record. Returns whether one was found."""
    from app.adapters.registry import get_storage_adapter

    claim = await claim_next()
    if claim is None:
        return False
    adapter = get_storage_adapter()
    result: Result
    try:
        async with asyncio.timeout(_settings().file_verification_job_deadline_seconds):
            result = await (_discover(claim) if claim.discovery else _verify(claim, adapter))
    except LeaseLost:
        logger.info("File %s: verification lease taken over; stopping this attempt", claim.file_id)
        return True
    except Exception as exc:
        code, transient = _classify(exc)
        if code == "storage_unavailable":
            logger.warning("File %s verification attempt failed", claim.file_id, exc_info=True)
        result = Result(error=code, transient=transient)
    if await complete(claim, result) != "applied":
        await _discard_artifact(adapter, result)
    return True


def _load_default_resolvers() -> None:
    try:
        from app.services.fetchngs_ingest_service import resolve_fetchngs_expectation
    except ImportError:
        return
    DISCOVERY_RESOLVERS.setdefault("fetchngs_csv", resolve_fetchngs_expectation)


async def run_worker_loop(stop_event: asyncio.Event | None = None) -> None:
    """The owned poll loop: ``file_verification_concurrency`` pollers (one by default), each with at
    most one active job, all paused during a restore review. Claims are leased and skip locked rows,
    so pollers never share a record."""
    _load_default_resolvers()
    concurrency = _settings().file_verification_concurrency
    logger.info("file verification worker started (concurrency %d)", concurrency)
    await asyncio.gather(*(_poll(stop_event) for _ in range(concurrency)))
    logger.info("file verification worker stopped")


async def _poll(stop_event: asyncio.Event | None) -> None:
    from app.tasks.periodic import wait_or_stop

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        owner = runtime.current_owner()
        found = False
        if owner is None or owner.paused is None:
            try:
                with owner.activity("file-verification-worker") if owner is not None else nullcontext():
                    found = await run_once()
            except Exception:
                logger.exception("file verification worker iteration failed")
        if found:
            continue
        if await wait_or_stop(POLL_INTERVAL_SECONDS, stop_event):
            break
