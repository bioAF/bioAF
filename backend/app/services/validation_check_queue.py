"""plan_8_1 section 3.2: checks as durable records, executed through a queue that keeps every result.

A study evaluated each claim's four checks and then ran ONE, writing its evidence into single-slot
artifacts. This keeps a record per plan, claim and check kind (``ValidationCheckRecord``) with its own
revisions, attempts and outcome, so nothing one check produces overwrites another.

**Revisions.** A record's dependencies (the claim's predicate, the input and its checksum, the sample
mapping, the reference, the workflow and its parameters, the source listing) are fingerprinted. A change
supersedes only the records whose dependencies include it: the revision it replaces is kept whole in
``history`` and the record goes back to ``pending``. Every other record stays current.

**Execution.** A check that launches no analysis workflow runs from the queue within bioAF's limits for
checks before approval. A workflow check runs only when an approval covers it (``approve``), and its
launch carries an idempotency key made of its ``analysis_key`` and ``approval_id``: a retry or a restart
reuses the execution already launched, and checks sharing an analysis key share one execution. Credit
stays per finding, so sharing never multiplies it.

**Failure (plan_8_2 section 1.3).** A transport failure is retried a bounded number of times, with the
count and the next attempt held on the record so restarts and other workers keep them. A failure to
interpret what arrived is terminal until the evidence or bioAF changes; it is never downloaded again. A
check whose result could not be stored gets a minimal safe record. Each stop records its terminal reason.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.validation_check_record import ValidationCheckRecord
from app.services.table_decoding import json_safe
from app.services.validation_acquisition_outcome import BACKOFF_SECONDS, MAX_ATTEMPTS

# The four checks a claim is evaluated for (change_7.5 section 2.5).
QC_METRIC = "qc_metric"
AUTHOR_RESULTS = "author_results"
PROCESSED_REANALYSIS = "processed_reanalysis"
RAW_REANALYSIS = "raw_reanalysis"
WORKFLOW_KINDS = (PROCESSED_REANALYSIS, RAW_REANALYSIS)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
BLOCKED = "blocked"
UNRESOLVED = "unresolved"
INTERRUPTED = "interrupted"
SUPERSEDED = "superseded"
STATES = (PENDING, RUNNING, DONE, BLOCKED, UNRESOLVED, INTERRUPTED, SUPERSEDED)
# plan_8_2 section 1.3: what the queue shows for a pending record that already failed to reach its input.
RETRYING = "retrying"

# Why a check stopped for good. Each is terminal until its evidence, or bioAF, changes.
INTERPRETATION = "interpretation"  # what arrived could not be interpreted
BINDING = "binding"  # which table reports the claim is not established
RETRIES_EXHAUSTED = "retries_exhausted"
PERSISTENCE_FAILED = "persistence_failed"
ERROR = "error"
LIMIT = "limit"
ACCESS_REFUSED = "access_refused"
UNAVAILABLE = "unavailable"
TERMINAL_REASONS = (
    INTERPRETATION,
    BINDING,
    RETRIES_EXHAUSTED,
    PERSISTENCE_FAILED,
    ERROR,
    LIMIT,
    ACCESS_REFUSED,
    UNAVAILABLE,
)

# The acquisition policy's bound: three attempts over roughly a quarter of an hour.
MAX_TRANSPORT_ATTEMPTS = MAX_ATTEMPTS
RETRY_BACKOFF_SECONDS = BACKOFF_SECONDS

# plan_8_2 labels, pending the owner's sign-off.
PERSISTENCE_REASON = "bioAF could not record this check's result; the details are in bioAF's log"
ERROR_REASON = "bioAF could not run this check; the details are in bioAF's log"


class NotApproved(Exception):
    """A workflow check was asked to run with no approval covering it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def due(record: ValidationCheckRecord, now: datetime | None = None) -> bool:
    """A pending record whose retry time, if it has one, has passed."""
    if record.state != PENDING:
        return False
    at = record.next_attempt_at
    return at is None or at <= (now or _utcnow())


def activity_of(record) -> str:
    """What the queue is doing with a record: its state, with a pending record that already failed to
    reach its input told apart as retrying."""
    state = record.state if not isinstance(record, dict) else record.get("state")
    retries = record.retry_count if not isinstance(record, dict) else record.get("retry_count")
    if state == PENDING and (retries or 0) > 0:
        return RETRYING
    return state


# plan_8_4 defect 4: a record's STATE is what the QUEUE did with it; its OUTCOME is what the CHECK
# established. They are not the same fact and must never be counted as one. A comparison that ran,
# read its table and could not settle the claim finishes in state `done` carrying an outcome of
# `unresolved`: finished work, and no conclusion. Counting it as a check concluded tells a reader
# the paper was checked when nothing about it was established.
INCONCLUSIVE_OUTCOMES = ("unresolved", "not_checkable")


def outcome_of(record) -> str | None:
    """The outcome a record's check reached, or None where it reached none."""
    outcome = record.outcome_json if not isinstance(record, dict) else record.get("outcome")
    if not isinstance(outcome, dict):
        return None
    value = outcome.get("outcome")
    return value if isinstance(value, str) else None


def concluded(record) -> bool:
    """Whether a record finished AND settled its question.

    ``done`` alone is not enough: the check has to have reached an outcome, and that outcome has to
    be one that decided something. A record with no outcome at all has concluded nothing either.
    """
    state = record.state if not isinstance(record, dict) else record.get("state")
    if state != DONE:
        return False
    outcome = outcome_of(record)
    return outcome is not None and outcome not in INCONCLUSIVE_OUTCOMES


def backoff_for(retry_count: int) -> int:
    return RETRY_BACKOFF_SECONDS[max(0, min(retry_count - 1, len(RETRY_BACKOFF_SECONDS) - 1))]


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def launch_key(analysis_key: str | None, approval_id: str | None) -> str:
    """The idempotency key a workflow execution is launched under."""
    return fingerprint({"analysis_key": analysis_key, "approval_id": approval_id})


def check_id_for(plan_id: int, target_id: int, kind: str) -> str:
    return f"plan:{plan_id}:claim:{target_id}:{kind}"


def _snapshot(record: ValidationCheckRecord, *, reason: str | None = None) -> dict:
    return {
        "superseded_because": reason,
        "revision": record.revision,
        "state": SUPERSEDED,
        "prior_state": record.state,
        "dependencies": copy.deepcopy(record.dependencies_json),
        "dependencies_fingerprint": record.dependencies_fingerprint,
        "analysis_key": record.analysis_key,
        "approval_id": record.approval_id,
        "attempts": copy.deepcopy(record.attempts_json or []),
        "outcome": copy.deepcopy(record.outcome_json),
        "superseded_at": _now(),
    }


def _supersede(
    record: ValidationCheckRecord, dependencies: dict, *, analysis_key: str | None, reason: str | None = None
) -> None:
    record.history_json = list(record.history_json or []) + [_snapshot(record, reason=reason)]
    record.revision = int(record.revision or 1) + 1
    record.dependencies_json = dependencies
    record.dependencies_fingerprint = fingerprint(dependencies)
    record.analysis_key = analysis_key
    record.approval_id = None
    record.state = PENDING
    record.attempts_json = []
    record.outcome_json = None
    # plan_8_2 section 1.3: a new revision is a new question, with the whole retry bound ahead of it.
    record.retry_count = 0
    record.next_attempt_at = None
    record.terminal_reason = None
    record.outcome_revision = int(record.outcome_revision or 0) + 1


async def records_for(session, study_id: int, *, kind: str | None = None) -> list[ValidationCheckRecord]:
    query = select(ValidationCheckRecord).where(ValidationCheckRecord.validation_study_id == study_id)
    if kind is not None:
        query = query.where(ValidationCheckRecord.kind == kind)
    return list((await session.execute(query.order_by(ValidationCheckRecord.id))).scalars().all())


async def ensure_record(
    session,
    study,
    plan,
    target,
    kind: str,
    dependencies: dict,
    *,
    analysis_key: str | None = None,
    reason: str | None = None,
) -> ValidationCheckRecord:
    """The current record for this plan, claim and check, created pending, or superseded to a new
    revision when what it depends on changed. Unchanged dependencies leave it as it is."""
    check_id = check_id_for(plan.id, target.id, kind)
    record = (
        await session.execute(select(ValidationCheckRecord).where(ValidationCheckRecord.check_id == check_id))
    ).scalar_one_or_none()
    if record is None:
        record = ValidationCheckRecord(
            validation_study_id=study.id,
            reproduction_plan_id=plan.id,
            comparison_target_id=target.id,
            check_id=check_id,
            kind=kind,
            revision=1,
            dependencies_json=dependencies,
            dependencies_fingerprint=fingerprint(dependencies),
            analysis_key=analysis_key,
            state=PENDING,
            attempts_json=[],
            history_json=[],
        )
        session.add(record)
        await session.flush()
        return record
    if record.dependencies_fingerprint != fingerprint(dependencies) or record.analysis_key != analysis_key:
        _supersede(record, dependencies, analysis_key=analysis_key, reason=reason or "what it depends on changed")
        await session.flush()
    return record


async def supersede(session, record: ValidationCheckRecord, *, reason: str) -> None:
    """plan_8_2 section 2.1: re-evaluate a record whose dependencies did not change (a recovery). The
    revision it replaces is kept whole in ``history`` with the reason."""
    _supersede(record, dict(record.dependencies_json or {}), analysis_key=record.analysis_key, reason=reason)
    await session.flush()


async def invalidate(session, study_id: int, dependency: str, value) -> list[ValidationCheckRecord]:
    """A dependency changed: supersede the records whose dependencies include it with another value.
    Returns the records it moved; every other record stays current."""
    moved = []
    for record in await records_for(session, study_id):
        deps = record.dependencies_json or {}
        if dependency not in deps or fingerprint(deps[dependency]) == fingerprint(value):
            continue
        _supersede(record, {**deps, dependency: value}, analysis_key=record.analysis_key)
        moved.append(record)
    if moved:
        await session.flush()
    return moved


async def start(session, record: ValidationCheckRecord, *, execution_ref: str | None = None, **fields) -> dict:
    attempt = {
        "attempt": len(record.attempts_json or []) + 1,
        "started_at": _now(),
        "finished_at": None,
        "outcome": None,
        "error": None,
        "execution_ref": execution_ref,
        **fields,
    }
    record.attempts_json = json_safe(list(record.attempts_json or []) + [attempt])
    record.state = RUNNING
    record.last_attempted_at = _utcnow()
    await session.flush()
    return attempt


async def finish(
    session,
    record: ValidationCheckRecord,
    *,
    state: str,
    outcome: dict | None = None,
    error: str | None = None,
    terminal_reason: str | None = None,
) -> None:
    """Close the attempt in flight (opening one if none was started) with the check's outcome.

    plan_8_2 section 1.3: an unresolved or blocked record names why it stopped (``terminal_reason``).
    Diagnostics (the attempts, the error, the reason) are made safe for JSONB, so a diagnostic can never
    cost the record. Scientific content is stored as it is: a value the database rejects is the caller's
    safe-failure path, never silently repaired."""
    if state not in STATES:
        raise ValueError(f"{state!r} is not a check state")
    if terminal_reason is not None and terminal_reason not in TERMINAL_REASONS:
        raise ValueError(f"{terminal_reason!r} is not a terminal reason")
    attempts = list(record.attempts_json or [])
    if not attempts or attempts[-1].get("finished_at"):
        attempts.append({"attempt": len(attempts) + 1, "started_at": _now(), "execution_ref": None})
    attempts[-1] = {**attempts[-1], "finished_at": _now(), "outcome": state, "error": error}
    record.attempts_json = json_safe(attempts)
    record.state = state
    if isinstance(outcome, dict) and isinstance(outcome.get("reason"), str):
        outcome = {**outcome, "reason": json_safe(outcome["reason"])}
    record.outcome_json = outcome
    record.terminal_reason = terminal_reason if state in (UNRESOLVED, BLOCKED) else None
    record.next_attempt_at = None
    record.last_attempted_at = _utcnow()
    record.outcome_revision = int(record.outcome_revision or 0) + 1
    await session.flush()


async def retry_later(
    session, record: ValidationCheckRecord, *, error: str | None, exhausted_reason: str, now: datetime | None = None
) -> bool:
    """A transport failure: close the attempt and schedule the next one, or, when the bound is spent,
    conclude the record unresolved with ``exhausted_reason``. Returns True when the record concluded."""
    now = now or _utcnow()
    attempts = list(record.attempts_json or [])
    if not attempts or attempts[-1].get("finished_at"):
        attempts.append({"attempt": len(attempts) + 1, "started_at": now.isoformat(), "execution_ref": None})
    record.retry_count = int(record.retry_count or 0) + 1
    if record.retry_count >= MAX_TRANSPORT_ATTEMPTS:
        record.attempts_json = attempts
        await finish(
            session,
            record,
            state=UNRESOLVED,
            outcome={"outcome": "unresolved", "reason": exhausted_reason},
            error=error,
            terminal_reason=RETRIES_EXHAUSTED,
        )
        return True
    attempts[-1] = {**attempts[-1], "finished_at": now.isoformat(), "outcome": RETRYING, "error": error}
    record.attempts_json = json_safe(attempts)
    record.state = PENDING
    record.next_attempt_at = now + timedelta(seconds=backoff_for(record.retry_count))
    record.last_attempted_at = now
    await session.flush()
    return False


async def reconcile(session, study_id: int, *, execution_state) -> None:
    """After a restart: a running record with an execution reference is reconciled against that
    execution's real state (``execution_state(ref)``); one without is interrupted and queued again."""
    for record in await records_for(session, study_id):
        if record.state != RUNNING:
            continue
        attempts = list(record.attempts_json or [])
        ref = (attempts[-1] if attempts else {}).get("execution_ref")
        real = execution_state(ref) if ref else None
        if ref and real:
            continue
        if attempts:
            attempts[-1] = {**attempts[-1], "finished_at": _now(), "outcome": INTERRUPTED}
        record.attempts_json = attempts
        record.state = PENDING
    await session.flush()


async def approve(session, study_id: int, check_ids: list[str], *, approval_id: str) -> None:
    """The approved set: one approval covers these workflow checks."""
    for record in await records_for(session, study_id):
        if record.check_id in check_ids:
            record.approval_id = approval_id
    await session.flush()


async def _answer(value):
    return await value if inspect.isawaitable(value) else value


async def _set_execution_ref(session, record: ValidationCheckRecord, key: str, ref: str, **fields) -> None:
    """Attach a launched execution's reference to the attempt that holds its launch identity."""
    attempts = list(record.attempts_json or [])
    for position in range(len(attempts) - 1, -1, -1):
        if attempts[position].get("launch_key") == key and not attempts[position].get("execution_ref"):
            attempts[position] = {**attempts[position], "execution_ref": ref, "dispatch": "dispatched", **fields}
            break
    record.attempts_json = attempts
    record.state = RUNNING
    await session.flush()


async def execute(session, record: ValidationCheckRecord, *, launch, lookup=None, checkpoint=None) -> str:
    """Run a workflow check once. ``launch()`` returns the execution's reference and is called only
    when no execution under this record's launch key exists yet, here or on a record sharing its
    analysis key. Returns the execution reference.

    plan_8_2 section 1.3: the launch identity is persisted before the dispatch (``checkpoint`` commits
    it), so a worker that stopped between the provider accepting the launch and the reference being
    recorded leaves a ``dispatching`` attempt. The next call asks the provider (``lookup(key)``) for an
    execution under that identity before launching, and launches under the same identity only when there
    is none."""
    if record.kind in WORKFLOW_KINDS and not record.approval_id:
        raise NotApproved(f"{record.check_id} is not in an approved set")
    key = launch_key(record.analysis_key, record.approval_id)
    for attempt in reversed(record.attempts_json or []):
        if attempt.get("launch_key") == key and attempt.get("execution_ref"):
            return attempt["execution_ref"]
    dispatching = next(
        (
            a
            for a in reversed(record.attempts_json or [])
            if a.get("launch_key") == key and not a.get("execution_ref") and a.get("dispatch") == "dispatching"
        ),
        None,
    )
    if dispatching is not None and lookup is not None:
        found = await _answer(lookup(key))
        if found:
            await _set_execution_ref(session, record, key, found, reconciled=True)
            return found
    if record.analysis_key:
        for other in await records_for(session, record.validation_study_id):
            if other.id == record.id or other.analysis_key != record.analysis_key:
                continue
            shared = next(
                (
                    a
                    for a in reversed(other.attempts_json or [])
                    if a.get("launch_key") == key and a.get("execution_ref")
                ),
                None,
            )
            if shared is not None:
                await start(
                    session, record, execution_ref=shared["execution_ref"], launch_key=key, shared_with=other.check_id
                )
                return shared["execution_ref"]
    if dispatching is None:
        await start(session, record, execution_ref=None, launch_key=key, dispatch="dispatching")
    if checkpoint is not None:
        await checkpoint()
    ref = await launch()
    await _set_execution_ref(session, record, key, ref)
    return ref


def record_dict(record: ValidationCheckRecord) -> dict:
    """A record as the report projection reads it."""
    return {
        "check_id": record.check_id,
        "kind": record.kind,
        "comparison_target_id": record.comparison_target_id,
        "revision": record.revision,
        "state": record.state,
        "outcome": record.outcome_json,
        "attempts": list(record.attempts_json or []),
        "dependencies": record.dependencies_json,
        "analysis_key": record.analysis_key,
        "approval_id": record.approval_id,
        "history": list(record.history_json or []),
        # plan_8_2 sections 1.3 and 1.4.
        "retry_count": int(record.retry_count or 0),
        "next_attempt_at": record.next_attempt_at.isoformat() if record.next_attempt_at else None,
        "terminal_reason": record.terminal_reason,
        "outcome_revision": int(record.outcome_revision or 0),
        "activity": activity_of(record),
    }
