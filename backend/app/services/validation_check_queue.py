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
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.validation_check_record import ValidationCheckRecord

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


class NotApproved(Exception):
    """A workflow check was asked to run with no approval covering it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def launch_key(analysis_key: str | None, approval_id: str | None) -> str:
    """The idempotency key a workflow execution is launched under."""
    return fingerprint({"analysis_key": analysis_key, "approval_id": approval_id})


def check_id_for(plan_id: int, target_id: int, kind: str) -> str:
    return f"plan:{plan_id}:claim:{target_id}:{kind}"


def _snapshot(record: ValidationCheckRecord) -> dict:
    return {
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


def _supersede(record: ValidationCheckRecord, dependencies: dict, *, analysis_key: str | None) -> None:
    record.history_json = list(record.history_json or []) + [_snapshot(record)]
    record.revision = int(record.revision or 1) + 1
    record.dependencies_json = dependencies
    record.dependencies_fingerprint = fingerprint(dependencies)
    record.analysis_key = analysis_key
    record.approval_id = None
    record.state = PENDING
    record.attempts_json = []
    record.outcome_json = None


async def records_for(session, study_id: int, *, kind: str | None = None) -> list[ValidationCheckRecord]:
    query = select(ValidationCheckRecord).where(ValidationCheckRecord.validation_study_id == study_id)
    if kind is not None:
        query = query.where(ValidationCheckRecord.kind == kind)
    return list((await session.execute(query.order_by(ValidationCheckRecord.id))).scalars().all())


async def ensure_record(
    session, study, plan, target, kind: str, dependencies: dict, *, analysis_key: str | None = None
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
        _supersede(record, dependencies, analysis_key=analysis_key)
        await session.flush()
    return record


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
    record.attempts_json = list(record.attempts_json or []) + [attempt]
    record.state = RUNNING
    await session.flush()
    return attempt


async def finish(
    session, record: ValidationCheckRecord, *, state: str, outcome: dict | None = None, error: str | None = None
) -> None:
    """Close the attempt in flight (opening one if none was started) with the check's outcome."""
    if state not in STATES:
        raise ValueError(f"{state!r} is not a check state")
    attempts = list(record.attempts_json or [])
    if not attempts or attempts[-1].get("finished_at"):
        attempts.append({"attempt": len(attempts) + 1, "started_at": _now(), "execution_ref": None})
    attempts[-1] = {**attempts[-1], "finished_at": _now(), "outcome": state, "error": error}
    record.attempts_json = attempts
    record.state = state
    record.outcome_json = outcome
    await session.flush()


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


async def execute(session, record: ValidationCheckRecord, *, launch) -> str:
    """Run a workflow check once. ``launch()`` returns the execution's reference and is called only
    when no execution under this record's launch key exists yet, here or on a record sharing its
    analysis key. Returns the execution reference."""
    if record.kind in WORKFLOW_KINDS and not record.approval_id:
        raise NotApproved(f"{record.check_id} is not in an approved set")
    key = launch_key(record.analysis_key, record.approval_id)
    for attempt in reversed(record.attempts_json or []):
        if attempt.get("launch_key") == key and attempt.get("execution_ref"):
            return attempt["execution_ref"]
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
    ref = await launch()
    await start(session, record, execution_ref=ref, launch_key=key)
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
    }
