"""Removing a File's superseded object after its new reference commits (project 12B.1c).

Moving a File's bytes is a copy, a commit, then a cleanup: the copy publishes the new object; the
caller's transaction records the new ``storage_uri`` together with ``pending_storage_cleanup`` (the
old object's pinned identity, ``record_relocation``); only after that commits is the old object
deleted (``cleanup_after_commit``, and ``reconcile_due`` for anything a crash or failure left). A
rollback therefore keeps the original object and its reference intact, and a crash after the commit
leaves a durable record for the next pass, never an unreferenced copy nobody knows about.

Cleanup deletes only the exact recorded version (``delete_if_unchanged``), only while no other
record (another File, another pending cleanup, an unfinished upload) names that object, and only
while storage writes are admitted. A replaced object or a shared one is kept and the record
cleared with a diagnostic; a storage failure is retried on a bounded schedule and then left for an
administrator, still recorded. This is the cleanup slot of the workflows that move File bytes, not a
general garbage collector.

Locks follow the verifier's order: the File's verification record, then the File.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import ObjectChanged, ObjectIdentity
from app.exceptions import StateError
from app.models.file import File
from app.models.file_verification import FileVerification
from app.models.upload_checkpoint import UploadCheckpoint

logger = logging.getLogger("bioaf.storage_cleanup")

RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

# Checkpoints in these stages no longer claim their recorded objects.
_SETTLED_UPLOAD_STAGES = ("filed", "cancelled", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _factory():
    from app import database

    return database.async_session_factory


def cleanup_slot(identity: ObjectIdentity) -> dict:
    return {
        "uri": identity.uri,
        "version": identity.version,
        "size_bytes": identity.size_bytes,
        "attempts": 0,
        "next_attempt_at": None,
    }


def _live(query):
    return query.where(File.deleted_at.is_(None), File.storage_deleted.is_(False))


async def lock_file_for_storage_change(
    session: AsyncSession, file_id: int, org_id: int, *, include_deleted: bool = False
) -> File | None:
    """Lock one of the organization's Files for a change to its stored bytes; None if it has none.

    The organization is trusted (the caller's principal or upload checkpoint), never inferred from
    the File. Takes the verification record's lock first, the verifier's order, so a relocation or
    deletion never deadlocks against a verifier publishing its result.
    """
    scoped = select(File.id).where(File.id == file_id, File.organization_id == org_id)
    if not include_deleted:
        scoped = _live(scoped)
    if await session.scalar(scoped) is None:
        return None
    await session.execute(select(FileVerification.id).where(FileVerification.file_id == file_id).with_for_update())
    query = select(File).where(File.id == file_id, File.organization_id == org_id)
    if not include_deleted:
        query = _live(query)
    return (
        await session.execute(query.with_for_update().execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def record_relocation(
    session: AsyncSession, file: File, *, destination: ObjectIdentity, previous: ObjectIdentity | None
) -> None:
    """Point ``file`` at ``destination`` and record ``previous`` for cleanup after the caller commits.

    ``file`` must be locked (``lock_file_for_storage_change``). A pending cleanup from an earlier
    relocation is settled first so work never accumulates; one that cannot be settled now refuses
    this relocation (StateError) rather than forgetting it. Verification follows the bytes.
    """
    from app.services.file_verification_service import FileVerificationService

    if file.pending_storage_cleanup:
        outcome = await clean_pending(session, file)
        if outcome in ("retry", "paused"):
            raise StateError("An earlier move of this file is still being tidied up. Try again shortly.")
    file.storage_uri = destination.uri
    file.pending_storage_cleanup = (
        cleanup_slot(previous) if previous is not None and previous.uri != destination.uri else None
    )
    await session.flush()
    await FileVerificationService.relocate(session, file, destination)


async def _other_claims(session: AsyncSession, file: File, uri: str) -> bool:
    """Whether anything but ``file`` still names ``uri``. Across organizations: an internal safety
    check whose answer is never shown to anyone."""
    other_file = await session.scalar(
        select(func.count(File.id)).where(
            File.id != file.id,
            or_(
                and_(
                    or_(File.storage_uri == uri, File.gcs_uri == uri),
                    File.deleted_at.is_(None),
                    File.storage_deleted.is_(False),
                ),
                File.pending_storage_cleanup["uri"].astext == uri,
            ),
        )
    )
    if other_file:
        return True
    upload = await session.scalar(
        select(func.count(UploadCheckpoint.id)).where(
            UploadCheckpoint.stage.not_in(_SETTLED_UPLOAD_STAGES),
            or_(UploadCheckpoint.source_uri == uri, UploadCheckpoint.planned_destination == uri),
        )
    )
    return bool(upload)


async def clean_pending(session: AsyncSession, file: File) -> str:
    """Try ``file``'s pending cleanup now, on the caller's session (the caller commits).

    ``file`` must be locked. Returns ``none``, ``deleted``, ``absent`` (already gone), ``current``
    (the record names the File's own object; never deleted), ``referenced`` or ``changed`` (kept,
    record cleared), ``paused`` or ``retry`` (record kept).
    """
    from app.adapters.registry import get_storage_adapter
    from app.services.storage_write_admission import StorageWritesPaused, admitted

    slot = file.pending_storage_cleanup
    if not slot:
        return "none"
    identity = ObjectIdentity(uri=slot["uri"], size_bytes=int(slot.get("size_bytes") or 0), version=slot["version"])
    if identity.uri in (file.storage_uri, file.gcs_uri) and not (file.deleted_at or file.storage_deleted):
        file.pending_storage_cleanup = None
        return "current"
    if await _other_claims(session, file, identity.uri):
        logger.warning("File %s: superseded object is still referenced; kept", file.id)
        file.pending_storage_cleanup = None
        return "referenced"
    try:
        async with admitted(session, error=StorageWritesPaused):
            deleted = await get_storage_adapter().delete_if_unchanged(identity)
    except StorageWritesPaused:
        return "paused"
    except ObjectChanged:
        logger.warning("File %s: superseded object was replaced since it was copied; kept", file.id)
        file.pending_storage_cleanup = None
        return "changed"
    except Exception as exc:
        attempts = int(slot.get("attempts") or 0) + 1
        due = _now() + RETRY_DELAYS[attempts - 1] if attempts < MAX_ATTEMPTS else None
        file.pending_storage_cleanup = {
            **slot,
            "attempts": attempts,
            "next_attempt_at": due.isoformat() if due else None,
        }
        # Only the type: storage errors can carry URLs and credentials.
        logger.warning(
            "File %s: superseded object could not be removed (attempt %d, error_type=%s)",
            file.id,
            attempts,
            type(exc).__name__,
        )
        return "retry"
    file.pending_storage_cleanup = None
    return "deleted" if deleted else "absent"


async def cleanup_after_commit(file_id: int, org_id: int, session_factory=None) -> str:
    """Run one File's pending cleanup in its own transaction, after the relocating caller committed."""
    factory = session_factory or _factory()
    async with factory() as session:
        file = await lock_file_for_storage_change(session, file_id, org_id, include_deleted=True)
        if file is None:
            await session.rollback()
            return "none"
        outcome = await clean_pending(session, file)
        await session.commit()
        return outcome


async def reconcile_due(session_factory=None, *, limit: int = 20) -> int:
    """Run pending cleanups whose turn has come (the periodic/startup pass). Returns how many settled."""
    factory = session_factory or _factory()
    now = _now()
    async with factory() as session:
        rows = (
            await session.execute(
                select(File.id, File.organization_id, File.pending_storage_cleanup)
                .where(File.pending_storage_cleanup.is_not(None))
                .order_by(File.id)
                .limit(limit * 5)
            )
        ).all()
    due = []
    for file_id, org_id, slot in rows:
        attempts = int((slot or {}).get("attempts") or 0)
        when = (slot or {}).get("next_attempt_at")
        if attempts >= MAX_ATTEMPTS:
            continue  # out of automatic attempts; still recorded for an administrator
        if attempts and (when is None or datetime.fromisoformat(when) > now):
            continue
        due.append((file_id, org_id))
        if len(due) >= limit:
            break
    settled = 0
    for file_id, org_id in due:
        try:
            outcome = await cleanup_after_commit(file_id, org_id, factory)
        except Exception as exc:
            logger.warning("Pending storage cleanup failed for file %s (error_type=%s)", file_id, type(exc).__name__)
            continue
        if outcome not in ("retry", "paused", "none"):
            settled += 1
    return settled
