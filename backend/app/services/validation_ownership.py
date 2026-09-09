"""change_7.2 section 2: own a study before reading it.

`POST /{id}/read` and the driver's `_handle_requested` both ran `read_and_plan` on the same study.
The request's transaction opened at 10:29:27 and committed at 10:30:27; the driver's opened at
10:29:30, blocked on the study row, and then ran a second full extraction from 10:30:27 to 10:31:10.
An approval at 10:31:06 blocked behind that and took 4,670ms. Every recent study carries two plans
as a result, and every run paid for two extractions.

`read_and_plan` guards on `study.state != "requested"`, but the guard is evaluated against state the
other transaction has not committed. `advance_active_studies` selects study ids with no row lock and
commits per study, so a lock taken on that first query would be released at the first commit. There
is no lease, claim or `with_for_update` anywhere else in the application; this mechanism is new.

**The whole protocol, not a column.**

- *Atomic acquisition.* A conditional UPDATE that succeeds for exactly one caller. Reading a row and
  then writing a holder is the bug being fixed, not the fix.
- *Renewal.* Extraction has been observed at 100 seconds across two model calls, which is longer than
  any safe fixed lease, and a lease that expires under a running worker recreates the duplicate it
  was added to prevent.
- *A fencing token, checked at write time.* An expired worker that finishes late must not be able to
  land its result.
- *Re-read after claiming.* The state that justified the attempt may have changed while the claim was
  contended.
- *Cancel and resume use the same protocol.* A cancellation that does not invalidate the running
  claim can be overwritten by a late extraction, which would silently undo it.

**Fencing a database write does not prevent a duplicate external operation.** A rejected write does
not recall a Kubernetes job that has already launched. So an operation identity is persisted BEFORE
dispatch, reused across retries, and ownership is checked immediately before dispatching rather than
only before writing. After a restart, a running operation is adopted rather than replaced.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.validation_study import ValidationStudy

logger = logging.getLogger("bioaf.validation_ownership")

# Short enough that a crashed worker's study is picked up again within a tick or two, long enough
# that a slow but healthy step does not lose its claim between renewals.
DEFAULT_LEASE_SECONDS = 120
# Renew well inside the lease: a single missed renewal must not expire a claim under running work.
_RENEW_EVERY = 30

# Operation lifecycle. `dispatching` is the window a crash can land in, and it is exactly why the
# identity is written first: a record in that state means an operation may or may not exist, which
# is a question to ask the provider rather than a licence to launch another.
OP_DISPATCHING = "dispatching"
OP_RUNNING = "running"
OP_DONE = "done"
OP_ABANDONED = "abandoned"


class ClaimLost(Exception):
    """A write was attempted under a claim that has expired or been superseded.

    Raised rather than returned: a caller that ignores this lands the result of work whose study
    somebody else now owns, which is the duplicate this protocol exists to prevent.
    """


@dataclass(frozen=True)
class Claim:
    study_id: int
    token: str
    holder: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def acquire(
    session: AsyncSession,
    study_id: int,
    *,
    holder: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    commit: bool = True,
) -> Claim | None:
    """Take the claim, or return None because somebody else holds it.

    One statement. The conditional UPDATE is what makes this atomic: a second caller's identical
    statement blocks on the row until the first commits and then matches nothing.
    """
    token = str(uuid.uuid4())
    now = _now()
    result = await session.execute(
        update(ValidationStudy)
        .where(
            ValidationStudy.id == study_id,
            (ValidationStudy.claim_token.is_(None)) | (ValidationStudy.claim_expires_at < now),
        )
        .values(claim_token=token, claim_holder=holder, claim_expires_at=now + timedelta(seconds=lease_seconds))
        .returning(ValidationStudy.id)
    )
    won = result.scalar_one_or_none() is not None
    if commit:
        # The claim has to be visible to the other writer before the long work starts, and the long
        # work must not run inside the transaction that took it.
        await session.commit()
    if not won:
        return None
    return Claim(study_id=study_id, token=token, holder=holder)


async def renew(session: AsyncSession, claim: Claim, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Extend a claim that is still ours. False means it was lost while the work ran."""
    result = await session.execute(
        update(ValidationStudy)
        .where(ValidationStudy.id == claim.study_id, ValidationStudy.claim_token == claim.token)
        .values(claim_expires_at=_now() + timedelta(seconds=lease_seconds))
        .returning(ValidationStudy.id)
    )
    return result.scalar_one_or_none() is not None


async def held(session: AsyncSession, claim: Claim) -> bool:
    token = (
        await session.execute(select(ValidationStudy.claim_token).where(ValidationStudy.id == claim.study_id))
    ).scalar_one_or_none()
    return token == claim.token


async def assert_held(session: AsyncSession, claim: Claim | None) -> None:
    """The fence. Called immediately before a write, and immediately before a dispatch.

    A claim of None is the un-owned legacy path and is allowed through: this is added underneath
    existing callers, and refusing every unclaimed write would stop the application rather than
    protect it.
    """
    if claim is None:
        return
    if not await held(session, claim):
        raise ClaimLost(f"study {claim.study_id}: the claim held by {claim.holder} is no longer current")


async def release(session: AsyncSession, claim: Claim | None, *, commit: bool = False) -> None:
    """Give the claim up, if it is still ours. A lost claim is already someone else's to release."""
    if claim is None:
        return
    await session.execute(
        update(ValidationStudy)
        .where(ValidationStudy.id == claim.study_id, ValidationStudy.claim_token == claim.token)
        .values(claim_token=None, claim_holder=None, claim_expires_at=None)
    )
    if commit:
        await session.commit()


async def invalidate(session: AsyncSession, study_id: int) -> None:
    """Break whatever claim is outstanding, so a late worker's write is fenced out.

    This is what cancel uses: a cancellation that leaves the running claim valid can be overwritten
    by a late extraction, which would silently undo it.
    """
    await session.execute(
        update(ValidationStudy)
        .where(ValidationStudy.id == study_id)
        .values(claim_token=None, claim_holder=None, claim_expires_at=None)
    )


@asynccontextmanager
async def owned(
    session: AsyncSession,
    study_id: int,
    *,
    holder: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    renew_every: int = _RENEW_EVERY,
):
    """Hold a study for the duration of a block, renewing the claim while the work runs.

    Yields the ``Claim`` on success and ``None`` when somebody else holds it, so a caller can skip
    rather than duplicate. The renewal runs on its own session: the work's session is busy, and two
    concurrent statements on one async session is an error in SQLAlchemy, not a race.
    """
    claim = await acquire(session, study_id, holder=holder, lease_seconds=lease_seconds)
    if claim is None:
        yield None
        return

    heartbeat = asyncio.create_task(_heartbeat(claim, lease_seconds=lease_seconds, every=renew_every))
    try:
        yield claim
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        # Released on its own connection. The caller's transaction may be mid-rollback after a
        # failure, and a release that gets rolled back with it leaves the study locked until the
        # lease expires, which is the hold this protocol exists to remove.
        with contextlib.suppress(Exception):
            from app.database import async_session_factory

            async with async_session_factory() as release_session:
                await release(release_session, claim, commit=True)


async def _heartbeat(claim: Claim, *, lease_seconds: int, every: int) -> None:
    """Renew a claim on its own connection until cancelled."""
    from app.database import async_session_factory

    while True:
        await asyncio.sleep(every)
        try:
            async with async_session_factory() as beat_session:
                still_ours = await renew(beat_session, claim, lease_seconds=lease_seconds)
                await beat_session.commit()
            if not still_ours:
                logger.warning("study %s: claim held by %s was lost while the work ran", claim.study_id, claim.holder)
                return
        except Exception as exc:  # noqa: BLE001 - a failed renewal must not kill the work it guards
            logger.warning("study %s: claim renewal failed: %s", claim.study_id, exc)


# ---- External operation identity (the half a database fence cannot cover) ----


def operation_of(study: ValidationStudy, key: str) -> dict | None:
    """The recorded identity of a logical external operation, or None if none was ever started."""
    return ((study.evidence_json or {}).get("operations") or {}).get(key)


async def begin_operation(
    session: AsyncSession,
    study: ValidationStudy,
    key: str,
    *,
    claim: Claim | None = None,
    kind: str = "",
) -> dict:
    """Persist an operation's identity BEFORE it is dispatched, and reuse it across retries.

    A crash between this write and the provider accepting the request leaves a record in
    ``dispatching``, which is a question to ask the provider rather than a licence to launch a
    second one.
    """
    await assert_held(session, claim)
    existing = operation_of(study, key)
    if existing and existing.get("status") in (OP_DISPATCHING, OP_RUNNING):
        return existing

    record = {
        "operation_id": str(uuid.uuid4()),
        "kind": kind or key,
        "status": OP_DISPATCHING,
        "started_at": _now().isoformat(),
        "external_id": None,
        "claimed_by": claim.holder if claim else None,
    }
    _write_operation(study, key, record)
    await session.flush()
    return record


async def record_dispatch(
    session: AsyncSession,
    study: ValidationStudy,
    key: str,
    *,
    external_id,
    claim: Claim | None = None,
) -> dict:
    """Attach the provider's own identifier once it has accepted the request."""
    await assert_held(session, claim)
    record = dict(operation_of(study, key) or {})
    record.update({"status": OP_RUNNING, "external_id": external_id, "dispatched_at": _now().isoformat()})
    _write_operation(study, key, record)
    await session.flush()
    return record


async def finish_operation(session: AsyncSession, study: ValidationStudy, key: str, *, status: str = OP_DONE) -> None:
    record = dict(operation_of(study, key) or {})
    if not record:
        return
    record.update({"status": status, "finished_at": _now().isoformat()})
    _write_operation(study, key, record)
    await session.flush()


def adoptable(study: ValidationStudy, key: str) -> dict | None:
    """An operation a previous worker started that this one must adopt rather than replace."""
    record = operation_of(study, key)
    if not record:
        return None
    if record.get("status") not in (OP_DISPATCHING, OP_RUNNING):
        return None
    return record


async def adopt(session: AsyncSession, study: ValidationStudy, key: str, *, by: str) -> dict:
    """Take over a running operation and record the adoption, rather than launching a second one."""
    record = dict(operation_of(study, key) or {})
    record["adopted_at"] = _now().isoformat()
    record["adopted_by"] = by
    record["status"] = OP_RUNNING
    _write_operation(study, key, record)
    await session.flush()
    return record


def _write_operation(study: ValidationStudy, key: str, record: dict) -> None:
    evidence = dict(study.evidence_json or {})
    operations = dict(evidence.get("operations") or {})
    operations[key] = record
    evidence["operations"] = operations
    study.evidence_json = evidence
