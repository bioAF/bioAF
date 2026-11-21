"""Settling direct uploads with the provider (storage lifecycle 11F/11G; review finding #2).

A direct upload may still write to storage until the provider confirms it cannot: a cancellation,
an abandoned session and a completion nobody reported are all settled here, never by a timeout
or by deleting a row.

- Session uploads (GCS resumable, S3 multipart): bioAF asks the provider. A cancelling or
  abandoned upload is cancelled with the provider; ``ENDED`` settles it, ``COMPLETED`` means the
  object landed first and it is removed (cancelled) or filed (never reported).
- Presigned URLs issued before upload sessions: after the URL's expiry plus one hour, the stored
  object decides. No object leaves the outcome unknown.
- When the provider cannot confirm an outcome after repeated tries, the upload keeps counting as
  a writer and is marked for an administrator, who may release it (audited, with an explicit
  acknowledgement that the transfer might still exist).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.models import UploadSessionState, UploadSessionStatus
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.upload_checkpoint import UploadCheckpoint

logger = logging.getLogger("bioaf.upload_settlement")

# A presigned URL may still be in use this long after it expires.
LEGACY_GRACE = timedelta(hours=1)
# An issued session with no new bytes for this long is abandoned and cancelled with the provider,
# so a closed browser tab does not keep Destroy Storage waiting. Browsers send in chunks (GCS) or
# parts (S3), so a running upload shows new bytes well within this.
ABANDON_AFTER = timedelta(hours=1)
# How often an active session is checked.
ACTIVE_CHECK = timedelta(minutes=5)
# Backoff between unanswered checks; after the last one the upload needs an administrator.
UNANSWERED_BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
)
NEEDS_RELEASE_AFTER = len(UNANSWERED_BACKOFF)
# Categories whose writer bioAF could not settle: an administrator may release them.
UNCONFIRMED = frozenset({"outcome_unknown"})
# An issued session whose handle was never recorded (the server stopped mid-issue).
_HANDLE_GRACE = timedelta(minutes=10)

SETTLING_STAGES = ("issued", "cancelling")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def first_check() -> datetime:
    return _now() + ACTIVE_CHECK


def settles_after(cp: UploadCheckpoint) -> datetime | None:
    """The earliest time bioAF expects to settle this writer, when it can say."""
    if cp.last_error_category in UNCONFIRMED:
        return None
    if cp.kind == "signed" and cp.grant_expires_at is not None:
        return cp.grant_expires_at + LEGACY_GRACE
    if cp.stage == "cancelling":
        return cp.next_reconcile_at
    return None


def needs_release(cp: UploadCheckpoint) -> bool:
    return cp.last_error_category in UNCONFIRMED


async def reconcile_due(session_factory, *, limit: int = 20) -> int:
    """Settle the uploads whose next check is due. Returns how many were checked."""
    now = _now()
    async with session_factory() as session:
        due = (
            (
                await session.execute(
                    select(UploadCheckpoint.id)
                    .where(
                        UploadCheckpoint.stage.in_(SETTLING_STAGES),
                        UploadCheckpoint.kind.in_(("session", "signed")),
                        or_(UploadCheckpoint.next_reconcile_at.is_(None), UploadCheckpoint.next_reconcile_at <= now),
                    )
                    .order_by(UploadCheckpoint.next_reconcile_at.nulls_first())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    for upload_id in due:
        try:
            await settle(session_factory, upload_id)
        except Exception as exc:
            logger.warning("Upload settlement check failed (error_type=%s)", type(exc).__name__)
    return len(due)


async def settle(session_factory, upload_id: str) -> str | None:
    """Check one upload with the provider and record what it confirms. Returns the stage after."""
    from app.services.upload_service import lock_upload

    async with session_factory() as session:
        await lock_upload(session, upload_id)
        cp = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
        if cp is None or cp.stage not in SETTLING_STAGES:
            return cp.stage if cp else None
        if cp.kind == "signed":
            await _settle_presigned(cp, session)
        elif cp.kind == "session":
            await _settle_session(cp, session)
        await session.commit()
        return cp.stage


async def _settle_session(cp: UploadCheckpoint, session: AsyncSession | None = None) -> None:
    from app.adapters.registry import get_storage_adapter

    now = _now()
    if not cp.session_handle:
        # The server stopped between creating the session and recording it: bioAF cannot reach it.
        if cp.created_at is not None and now - cp.created_at > _HANDLE_GRACE:
            _unanswered(cp, give_up=True)
        else:
            cp.next_reconcile_at = now + timedelta(minutes=1)
        return
    adapter = get_storage_adapter()
    cancelling = cp.stage == "cancelling"
    status = None
    if not cancelling:
        status = await adapter.inspect_upload_session(cp.source_uri, cp.session_handle)
        if status.state is UploadSessionState.ACTIVE:
            _record_progress(cp, status, now)
            if cp.progress_at is not None and now - cp.progress_at <= ABANDON_AFTER:
                cp.reconcile_attempts = 0
                cp.next_reconcile_at = now + ACTIVE_CHECK
                return
            logger.info("Upload %s had no new bytes for %s: cancelling it", cp.reference, ABANDON_AFTER)
            cp.stage = "cancelling"
            cp.last_error_category = "abandoned"
            cp.cancel_requested_at = now
            cancelling = True
    if cancelling:
        status = await adapter.cancel_upload_session(cp.source_uri, cp.session_handle)
    await _apply(cp, status, now, session)


async def _owned_by_another_upload(session: AsyncSession | None, cp: UploadCheckpoint) -> bool:
    """Whether another upload that is not settled away claims this object path.

    Upload paths are unique per upload, so this should never be true; if it is, the object at the
    path cannot be attributed to this upload, and it is neither deleted nor filed as this upload's.
    """
    if session is None:
        return False
    other = await session.scalar(
        select(UploadCheckpoint.id)
        .where(
            UploadCheckpoint.source_uri == cp.source_uri,
            UploadCheckpoint.id != cp.id,
            UploadCheckpoint.stage.in_(("issued", "transferring", "received", "filed")),
        )
        .limit(1)
    )
    return other is not None


async def _apply(
    cp: UploadCheckpoint, status: UploadSessionStatus, now: datetime, session: AsyncSession | None = None
) -> None:
    from app.adapters.registry import get_storage_adapter

    state = status.state
    if state is UploadSessionState.COMPLETED and await _owned_by_another_upload(session, cp):
        logger.warning("Upload %s: the object at its path belongs to another upload; not touching it", cp.reference)
        state = UploadSessionState.ENDED
    if state is UploadSessionState.COMPLETED:
        if cp.stage == "cancelling":
            # The last bytes landed before the cancellation: remove what was stored.
            await get_storage_adapter().delete(cp.source_uri)
            _settled(cp, "cancelled", _ended_category(cp))
        else:
            _received(cp, now)
        return
    if state is UploadSessionState.ENDED:
        _settled(cp, "cancelled", _ended_category(cp))
        return
    # ACTIVE while cancelling (a part still in flight) or no answer: not confirmed, check again.
    _unanswered(cp, give_up=False)


async def _settle_presigned(cp: UploadCheckpoint, session: AsyncSession | None = None) -> None:
    from app.adapters.registry import get_storage_adapter

    now = _now()
    grace_end = (cp.grant_expires_at or cp.created_at or now) + LEGACY_GRACE
    if now < grace_end:
        cp.next_reconcile_at = grace_end
        return
    try:
        stored = await get_storage_adapter().exists(cp.source_uri)
    except Exception as exc:
        logger.warning("Could not check a presigned upload's object (error_type=%s)", type(exc).__name__)
        _unanswered(cp, give_up=False)
        return
    if stored:
        status = UploadSessionStatus(state=UploadSessionState.COMPLETED)
        await _apply(cp, status, now, session)
        return
    # No object: the transfer may have ended, or may still be running. bioAF cannot tell. Keep
    # looking (an object that appears later is still handled) and let an administrator decide.
    cp.last_error_category = "outcome_unknown"
    cp.next_reconcile_at = now + timedelta(hours=1)


def _ended_category(cp: UploadCheckpoint) -> str:
    if cp.stage != "cancelling":
        return "expired"
    return "abandoned" if cp.last_error_category == "abandoned" else "cancelled"


def _record_progress(cp: UploadCheckpoint, status: UploadSessionStatus, now: datetime) -> None:
    received = status.received_bytes
    if received is not None and received > (cp.progress_bytes or 0):
        cp.progress_bytes = received
        cp.progress_at = now


def _received(cp: UploadCheckpoint, now: datetime) -> None:
    """The provider stored the whole object but nobody reported it: file it automatically.

    Only data file uploads are filed; a lab document or reference file is claimed by its own
    flow, so it just stops counting as a writer."""
    cp.stage = "received"
    cp.next_reconcile_at = None
    cp.next_attempt_at = now if cp.purpose == "file" else None
    cp.reconcile_attempts = 0
    logger.info("Upload %s finished without being reported; filing it", cp.reference)


def _settled(cp: UploadCheckpoint, stage: str, category: str) -> None:
    cp.stage = stage
    cp.last_error_category = category
    cp.next_reconcile_at = None
    cp.next_attempt_at = None
    logger.info("Upload %s settled: %s", cp.reference, category)


def _unanswered(cp: UploadCheckpoint, *, give_up: bool) -> None:
    attempts = (cp.reconcile_attempts or 0) + 1
    cp.reconcile_attempts = attempts
    if give_up or attempts >= NEEDS_RELEASE_AFTER:
        if cp.last_error_category not in UNCONFIRMED:
            logger.warning(
                "Upload %s: the provider did not confirm its outcome; it needs an administrator", cp.reference
            )
        cp.last_error_category = "outcome_unknown"
        cp.next_reconcile_at = _now() + timedelta(hours=1)
        return
    cp.next_reconcile_at = _now() + UNANSWERED_BACKOFF[attempts - 1]


async def release(
    session: AsyncSession, org_id: int, upload_id: str, user_id: int, *, reason: str, acknowledged: bool
) -> UploadCheckpoint:
    """An administrator's audited release of a writer whose outcome bioAF could not confirm.

    Only an upload marked as needing an administrator can be released: one the provider reports
    as running must be cancelled instead. The caller commits.
    """
    from app.services.audit_service import log_action
    from app.services.upload_service import lock_upload

    reason = (reason or "").strip()
    if not acknowledged:
        raise ValidationError("Confirm that you understand the transfer might still exist.")
    if not reason:
        raise ValidationError("A reason is required.")
    await lock_upload(session, upload_id)
    cp = await session.get(UploadCheckpoint, upload_id, populate_existing=True)
    if cp is None or cp.organization_id != org_id:
        raise NotFoundError("Upload not found")
    if cp.stage not in SETTLING_STAGES or not needs_release(cp):
        raise ConflictError("bioAF has not lost track of this upload, so it cannot be released.")
    now = _now()
    cp.stage = "cancelled"
    cp.released_at = now
    cp.released_by_user_id = user_id
    cp.release_reason = reason[:2000]
    cp.next_reconcile_at = None
    await log_action(
        session,
        user_id=user_id,
        entity_type="upload",
        entity_id=0,
        action="upload_writer_released",
        details={
            "upload_id": upload_id,
            "reference": cp.reference,
            "filename": cp.filename,
            "reason": cp.release_reason,
            "acknowledged_transfer_may_exist": True,
        },
    )
    await session.flush()
    logger.info("Upload %s released by an administrator", cp.reference)
    return cp
