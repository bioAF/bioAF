"""plan_8_1 sections 1.2 and 1.4: the durable record of one stage of a paper read.

A read's model calls are bounded: at most two submissions per cycle, the first attempt and one recovery
attempt shared across truncation and structural failure. A change of failure type does not reset the
count, and neither does a worker restart: each attempt is written, and committed by the caller, BEFORE
it is submitted. An attempt that has a start and no end was interrupted, and it still counts.

An explicit re-read starts a new cycle. The cycle it replaces moves to history whole, so what the earlier
read did stays on the record and can never mark the new plan as a failed read.

**Current provenance is authoritative.** A study carrying this record is judged by it: a succeeded cycle
is a read, whatever earlier issues say; a cycle in progress is not a terminal failure. The legacy rule
(``read_failed``) applies only to a study whose read predates the record.

Stage-agnostic: the extraction writes ``evidence["extraction"]``; stage 2's finding inventory writes
``evidence["inventory_stage"]`` the same way.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

MAX_SUBMISSIONS = 2

EXTRACTION_STAGE = "extraction"

IN_PROGRESS = "in_progress"
SUCCEEDED = "succeeded"
FAILED = "failed"

# An attempt's outcome, beside the decision outcomes ``llm_decision`` reports.
ATTEMPT_OK = "ok"
ATTEMPT_INCOMPLETE = "incomplete"
ATTEMPT_INTERRUPTED = "interrupted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_key(stage: str) -> str:
    return f"{stage}_history"


def current_cycle(evidence: dict | None, stage: str = EXTRACTION_STAGE) -> dict | None:
    cycle = (evidence or {}).get(stage)
    return cycle if isinstance(cycle, dict) else None


def _write(study, stage: str, cycle: dict, *, history: list | None = None) -> dict:
    evidence = dict(study.evidence_json or {})
    evidence[stage] = cycle
    if history is not None:
        evidence[_history_key(stage)] = history
    study.evidence_json = evidence
    return cycle


def begin_cycle(study, stage: str = EXTRACTION_STAGE, **fields) -> dict:
    """A new cycle for ``stage``. The one it replaces, if any, moves to history whole."""
    evidence = dict(study.evidence_json or {})
    previous = current_cycle(evidence, stage)
    history = list(evidence.get(_history_key(stage)) or [])
    if previous is not None:
        history.append(copy.deepcopy(previous))
    cycle = {
        "cycle": int((previous or {}).get("cycle") or 0) + 1,
        "status": IN_PROGRESS,
        "started_at": _now(),
        "finished_at": None,
        "attempts": [],
        **fields,
    }
    return _write(study, stage, cycle, history=history)


def mark_interrupted(study, stage: str = EXTRACTION_STAGE) -> dict | None:
    """Close every attempt that started and never ended: a worker stopped under it. It still counts."""
    cycle = copy.deepcopy(current_cycle(study.evidence_json, stage))
    if cycle is None:
        return None
    changed = False
    for attempt in cycle.get("attempts") or []:
        if not attempt.get("finished_at"):
            attempt.update(finished_at=_now(), outcome=ATTEMPT_INTERRUPTED)
            changed = True
    return _write(study, stage, cycle) if changed else cycle


def attempts_used(cycle: dict | None) -> int:
    return len((cycle or {}).get("attempts") or [])


def last_attempt(cycle: dict | None) -> dict | None:
    attempts = (cycle or {}).get("attempts") or []
    return attempts[-1] if attempts else None


def start_attempt(study, stage: str = EXTRACTION_STAGE, **fields) -> dict:
    """Record an attempt before it is submitted. The caller commits it before the call goes out."""
    cycle = copy.deepcopy(current_cycle(study.evidence_json, stage) or {})
    attempts = list(cycle.get("attempts") or [])
    record = {"attempt": len(attempts) + 1, "started_at": _now(), "finished_at": None, "outcome": None, **fields}
    attempts.append(record)
    cycle["attempts"] = attempts
    _write(study, stage, cycle)
    return record


def finish_attempt(study, stage: str = EXTRACTION_STAGE, **fields) -> dict:
    """Close the attempt in flight with what the call used and how it ended."""
    cycle = copy.deepcopy(current_cycle(study.evidence_json, stage) or {})
    attempts = list(cycle.get("attempts") or [])
    if not attempts:
        raise ValueError(f"no {stage} attempt is in flight")
    attempts[-1] = {**attempts[-1], "finished_at": _now(), **fields}
    cycle["attempts"] = attempts
    _write(study, stage, cycle)
    return attempts[-1]


def update_cycle(study, stage: str = EXTRACTION_STAGE, **fields) -> dict:
    """Record facts on the cycle in progress without finishing it."""
    cycle = copy.deepcopy(current_cycle(study.evidence_json, stage) or {})
    cycle.update(**fields)
    return _write(study, stage, cycle)


def finish_cycle(study, stage: str = EXTRACTION_STAGE, *, status: str, **fields) -> dict:
    cycle = copy.deepcopy(current_cycle(study.evidence_json, stage) or {})
    cycle.update(status=status, finished_at=_now(), **fields)
    return _write(study, stage, cycle)
