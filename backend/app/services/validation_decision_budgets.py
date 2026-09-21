"""plan_8_3 stage 6: the output budget, recovery policy and measurement evidence of every decision
bioAF asks a model to make.

plan_8_1 measured the paper read's two calls. Nothing else was budgeted, so eleven other decisions
still reached the Anthropic client's fixed 4,096-token default. Study 50 paid for it twice: all 16 of
its claim bindings failed after the answer was cut off, and its input choice was cut off before it
produced the saved proposal. Neither failure was a model refusing to answer; both were bioAF asking
for more than it allowed room for.

**Every call site names a purpose.** ``decide(purpose=...)`` resolves the budget from the registry
below, and ``tests/test_decision_budgets.py`` walks the application for ``decide`` calls, so a caller
added later that names no purpose fails the audit rather than inheriting a default nobody reviewed.

**A budget is a bound, not a promise.** Where the answer's size follows the input (one row per claim,
one row per matrix column) the registry records the measured case and the shape that scales, and the
caller batches so the count stays inside it. ``decide_with_recovery`` covers the rest: exactly one
semantic retry per decision, a larger budget for a cut-off answer and targeted feedback for one that
did not satisfy the caller's schema. Transport retries stay in the provider clients, below this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.validation_read_budget import (
    NOT_MEASURED_NOTE,
    Budget,
    model_output_limit,
    recovery_budget,
)

RECORDS = Path(__file__).resolve().parent / "read_measurements"

# What established a budget. `measured` names a record under read_measurements/; `contract` is a
# documented bound computed from the answer's shape; `pending_measurement` is a bound in force while
# a measurement on a real workload is outstanding, and says so on the provenance.
MEASURED = "measured"
CONTRACT = "contract"
PENDING_MEASUREMENT = "pending_measurement"
MEASUREMENT_STATES = (MEASURED, CONTRACT, PENDING_MEASUREMENT)

# What a semantic failure is allowed to cost. `one_retry` is this module's shared policy: a cut-off
# answer is asked again once with a larger budget, an answer the caller's schema rejects is asked
# again once with what was wrong. `caller_owned` means the caller runs its own bounded attempt cycle
# (the read's extraction and inventory persist their attempts across a restart, and must).
ONE_RETRY = "one_retry"
CALLER_OWNED = "caller_owned"
RECOVERY_POLICIES = (ONE_RETRY, CALLER_OWNED)

# An answer that arrived whole and did not satisfy the caller's schema, twice. It is not a truncation
# (nothing was cut off), not unparseable (it was JSON) and not a refusal.
OUTCOME_SCHEMA_REJECTED = "schema_rejected"

# A one-object answer with a couple of scalar fields and a sentence or two of reasoning. Measured
# answers of this shape run to a few hundred output tokens; the bound is generous because a model
# that reasons out loud before the fence still has to fit.
_FIXED_ANSWER = 4000
# An answer whose length follows the input: one row per claim, one row per matrix column, a script.
_ROW_PER_ITEM = 16000

PAPER_READING = "paper_reading"
FINDING_INVENTORY = "finding_inventory"
CLAIM_BINDING = "claim_binding"
DEPOSIT_SELECTION = "deposit_selection"
CONTRAST_SELECTION = "contrast_selection"
INPUT_CHOICE = "input_choice"
MAPPING_PROPOSAL = "mapping_proposal"
PRECOMPUTE_CHECK = "precompute_check"
RATIFICATION = "ratification"
ANALYSIS_SELECTION = "analysis_selection"
COLUMN_RESOLUTION = "column_resolution"
GENERATED_ANALYSIS = "generated_analysis"
CODE_EXECUTION = "code_execution"
SIGNAL_VERDICT = "signal_verdict"
SIGNAL_CANDIDATE = "signal_candidate"
# plan_8_5 section 3.6: one obligation of the rubric, judged on the paper's own passages.
DOCUMENTARY_JUDGMENT = "documentary_judgment"


@dataclass(frozen=True)
class DecisionPolicy:
    """One decision's entry in the audit: what it asks for, what it may spend, and what recovers it."""

    purpose: str
    decision: str
    module: str
    max_tokens: int
    recovery: str
    measurement: str
    basis: str
    record: str | None = None
    # plan_8_6 section 11: what one request may be GIVEN, beside what it may produce. Declared here
    # so the number is on the record with every other budget rather than living in a selector.
    max_input_tokens: int | None = None

    def provenance(self) -> dict:
        return {
            "purpose": self.purpose,
            "decision": self.decision,
            "max_tokens": self.max_tokens,
            "max_input_tokens": self.max_input_tokens,
            "recovery": self.recovery,
            "measurement": self.measurement,
            "record": self.record,
        }


def _policy(purpose: str, **kw) -> DecisionPolicy:
    return DecisionPolicy(purpose=purpose, **kw)


DECISIONS: dict[str, DecisionPolicy] = {
    p.purpose: p
    for p in (
        _policy(
            PAPER_READING,
            decision="Paper read (extraction)",
            module="app/services/validation_extraction_service.py",
            max_tokens=16000,
            recovery=CALLER_OWNED,
            measurement=MEASURED,
            record="extraction",
            basis="measured on four papers (plan_8_1 section 1.1); the caller owns the budget it sends and "
            "persists each attempt, so a restart does not reset its bound",
        ),
        _policy(
            FINDING_INVENTORY,
            decision="Finding inventory",
            module="app/services/validation_inventory_stage.py",
            max_tokens=16000,
            recovery=CALLER_OWNED,
            measurement=MEASURED,
            record="inventory",
            basis="measured on four papers (plan_8_1 section 2.2); the caller owns its own attempt cycle",
        ),
        _policy(
            CLAIM_BINDING,
            decision="Claim binding",
            module="app/services/validation_extraction_service.py",
            max_tokens=_ROW_PER_ITEM,
            recovery=CALLER_OWNED,
            measurement=PENDING_MEASUREMENT,
            record="claim_binding",
            basis="one row per claim (index, key, reason, confidence, declined): about 120 output tokens each, "
            "so the bound holds well over a hundred claims. The caller batches at BINDING_BATCH so the count "
            "stays inside it and owns the retry that carries the failed batch's feedback",
        ),
        _policy(
            DEPOSIT_SELECTION,
            decision="Deposit selection",
            module="app/services/deposit_selection.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: a filename, an optional metadata filename, a value type, a sentence and a "
            "confidence. Its length does not follow the deposit's size",
        ),
        _policy(
            CONTRAST_SELECTION,
            decision="Contrast selection",
            module="app/services/contrast_selection.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: an index, a sentence and a confidence",
        ),
        _policy(
            INPUT_CHOICE,
            decision="Initial input choice",
            module="app/services/validation_input_choice.py",
            max_tokens=_ROW_PER_ITEM,
            recovery=ONE_RETRY,
            measurement=PENDING_MEASUREMENT,
            record="input_choice",
            basis="one row per matrix column (column, arm, two identities, a technical group, a time point and "
            "its quoted evidence): about 150 output tokens each, so the bound holds a hundred columns. The "
            "previews it reads are capped at PREVIEW_FILES files",
        ),
        _policy(
            MAPPING_PROPOSAL,
            decision="Mapping proposal and recovery",
            module="app/services/validation_input_choice.py",
            max_tokens=_ROW_PER_ITEM,
            recovery=ONE_RETRY,
            measurement=PENDING_MEASUREMENT,
            record="input_choice",
            basis="the same answer shape as the input choice, over the acquired matrix's own columns",
        ),
        _policy(
            PRECOMPUTE_CHECK,
            decision="Precompute checks",
            module="app/services/validation_precompute_checks.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis='one object: "yes" or "no", a sentence and a confidence',
        ),
        _policy(
            RATIFICATION,
            decision="Ratification",
            module="app/services/validation_ratification.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: accept or override, a verdict, a sentence and a confidence",
        ),
        _policy(
            ANALYSIS_SELECTION,
            decision="Analysis selection",
            module="app/services/validation_selection.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: a claim index, a check name, a sentence and a confidence",
        ),
        _policy(
            COLUMN_RESOLUTION,
            decision="Column resolution",
            module="app/services/column_resolution.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object mapping a closed set of roles to column names; the roles are a handful, not the "
            "table's width",
        ),
        _policy(
            GENERATED_ANALYSIS,
            decision="Generated analysis",
            module="app/services/generated_analysis.py",
            max_tokens=_ROW_PER_ITEM,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="a complete R or Python script. The templates it replaces run to a few hundred lines, and a "
            "script cut off mid-statement is worse than no script",
        ),
        _policy(
            CODE_EXECUTION,
            decision="Code-execution decision",
            module="app/services/code_execution_service.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: an entry point, its arguments, a sentence and a confidence",
        ),
        _policy(
            SIGNAL_VERDICT,
            decision="First signal-assessment decision",
            module="app/services/signal_assessment.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: likely or not likely, one or two sentences and a confidence",
        ),
        _policy(
            SIGNAL_CANDIDATE,
            decision="Second signal-assessment decision",
            module="app/services/signal_assessment.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object: one listed candidate, one or two sentences and a confidence",
        ),
        _policy(
            DOCUMENTARY_JUDGMENT,
            decision="Documentary obligation judgment",
            module="app/services/validation_documentary_review.py",
            max_tokens=_FIXED_ANSWER,
            recovery=ONE_RETRY,
            measurement=CONTRACT,
            basis="one object about ONE obligation: an outcome, one or two sentences, the ids it cites "
            "and a confidence. It never rates a paper and never produces a number the score uses",
            # plan_8_6 section 11 says to start at 8,192 input tokens and, where the acceptance
            # evidence cannot fit, to report the tradeoff and propose a measured adjustment rather
            # than silently truncating. Measured on study 65 (eLife 83291, 98,246 characters of
            # text) at the deployed 8,192: every one of the seventeen judged obligations deferred
            # relevant evidence, so NO absence finding could be reported on it at all, which is the
            # whole of the repair section 8 exists for. The relevant evidence one obligation left
            # behind ran from 3,118 to 21,213 characters on top of a packet already at 21,000 to
            # 24,000, so carrying the largest of them whole needs about 11,700 input tokens.
            #
            # 16,384 is that, with room. The measured cost is a packet budget of 48,000 characters
            # against 24,000, so a full documentary review of a paper this size roughly doubles from
            # 373,187 characters of evidence across eighteen requests to about 750,000. A lower
            # organization limit still wins.
            max_input_tokens=16384,
        ),
    )
}


@lru_cache(maxsize=None)
def _record(name: str) -> dict:
    return json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))


def policy_for(purpose: str) -> DecisionPolicy:
    """The audit entry for ``purpose``. A purpose the audit does not name is a defect, not a default."""
    try:
        return DECISIONS[purpose]
    except KeyError:
        raise KeyError(
            f"{purpose} is not a decision this audit covers; add it to validation_decision_budgets.DECISIONS "
            "with its budget, recovery policy and the evidence for both"
        ) from None


def budget_for(purpose: str, model: str | None) -> Budget:
    """The budget ``purpose`` runs under on ``model``, never above the model's documented maximum."""
    policy = policy_for(purpose)
    measured = False
    if policy.record:
        try:
            measured = (model or "") in (_record(policy.record).get("models") or {})
        except (OSError, ValueError):
            measured = False
    return Budget(
        call=purpose,
        max_tokens=min(policy.max_tokens, model_output_limit(model)),
        measured=measured,
        note=None if measured else NOT_MEASURED_NOTE,
        fingerprint=None,
    )


def larger_budget(current: int, *, model: str | None, provider: str | None, purpose: str) -> int | None:
    """The budget a cut-off answer is asked again with, or None when there is no more room."""
    policy = policy_for(purpose)
    record: dict = {}
    if policy.record:
        try:
            record = _record(policy.record)
        except (OSError, ValueError):
            record = {}
    return recovery_budget(current, model=model, provider=provider, record=record)
