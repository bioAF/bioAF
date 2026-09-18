"""plan_8_1 sections 1.3 and 1.4: a read that failed is never a fact about the paper.

Studies 42 and 43 were cut off at the model's output limit. Their plans then said the paper had methods
too thin to name an assay, deposited no data, stated no reference and named no code, and each study was
classified ``missing_data``. None of it came from a read: every sentence was derived from a field the
answer never reached. The same SAMD1 report listed the deposit and the reference those sentences denied.

**One rule, used everywhere.** ``read_failure`` decides whether a study's current plan came from a failed
read. The report, the studies list, both exports, Retry and the recovery migration all ask it, so none can
disagree with another:

- A study carrying explicit extraction provenance (``evidence["extraction"]``) is judged by it. A failed
  cycle is a failed read; a succeeded one is not, whatever earlier issues say, even when it legitimately
  found no claims; a cycle still in progress is not a terminal failure.
- Only without that provenance (a study read before it existed), the legacy rule: the study's issues
  include the paper-reading step with impact ``blocked``, and its current plan holds no claims.
"""

from __future__ import annotations

BIOAF_LIMITATION = "bioaf_limitation"

# The step every read records its issues under (``validation_extraction_service.PAPER_READING_INTENT``).
PAPER_READING_STEP = "reading the paper and extracting its methods and claims"

# What each recorded outcome of the reading step means, for a read recorded before its cause was kept.
_LEGACY_CAUSES = {
    "truncated": "the model's answer was cut off at its token limit",
    "timed_out": "the model's answer did not finish within bioAF's time limit",
    "schema_rejected": "the model's answer did not hold what bioAF asked for",
    "refusal": "the model declined to answer",
    "unreachable": "bioAF could not reach the language model",
    # plan_8_3 stage 6: an account fact an administrator can act on, never a failure to reach the provider.
    "account": "the language model account bioAF uses could not run the request",
    "unparseable": "the model's answer was not in the format bioAF asked for",
    "incomplete": "the model's answer left out parts bioAF requires",
    "internal": "bioAF hit an internal error",
}

# plan_8_1 labels, pending the owner's sign-off.
PAPER_NOT_READ = "The paper was not read."
NOT_ESTABLISHED_BLOCKER = "Not established: the paper was not read."
CLASSIFICATION_NOTE = "This classification came from a read that failed, not from the paper."


def read_failure_blocker(cause: str) -> str:
    """The one blocker a failed read's plan carries."""
    return f"bioAF could not read the paper: {cause}. This is a bioAF limitation, not a finding about the paper."


def scorecard_reason(cause: str) -> str:
    """What the scorecard says of a failed read: the cause, whose limitation it is, and what to do."""
    return f"bioAF could not read the paper into findings: {cause}. This is a bioAF limitation; read the paper again."


def inventory_failure_reason(cause: str) -> str:
    """section 2.1: the scorecard's reason when the inventory stage could not establish the findings."""
    return f"bioAF could not group the paper's claims into findings: {cause}."


def omitted_part(part: str) -> str:
    """A part an otherwise usable answer left out, in the reader's words."""
    return f"Not read: the reading omitted {part}."


def read_failure(evidence: dict | None, *, issues: list[dict] | None, claim_count: int) -> dict | None:
    """``{"cause", "legacy"}`` when the study's current plan came from a failed read, else None."""
    cycle = (evidence or {}).get("extraction")
    if isinstance(cycle, dict):
        if cycle.get("status") == "failed":
            return {"cause": cycle.get("cause") or _LEGACY_CAUSES["internal"], "legacy": False}
        return None
    blocked = [
        issue
        for issue in issues or []
        if isinstance(issue, dict) and issue.get("step") == PAPER_READING_STEP and issue.get("impact") == "blocked"
    ]
    if blocked and claim_count == 0:
        outcome = str(blocked[-1].get("outcome") or "internal")
        return {"cause": _LEGACY_CAUSES.get(outcome, _LEGACY_CAUSES["internal"]), "legacy": True}
    return None
