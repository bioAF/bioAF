"""plan_8 sections 1 and 5: the Validation Scorecard, built once from the finding inventory and outcomes.

Two metrics, always shown together:

- **Overall score**, 0 to 100: agreement among conclusively assessed findings, a primary finding
  weighing twice as much as a supporting one (weighted rubric version 1). ``100 x W_supported /
  W_assessed``; null when nothing of positive weight was conclusively assessed.
- **Assessed scope**, ``X / N``: how many scoreable findings received a valid, conclusive independent
  assessment, unweighted.

Neither is a probability that the paper is correct, a grade of its science, or a claim that every
conclusion was reproduced. A finding bioAF could not assess (controlled access, data not deposited,
a bioAF limitation) stays in the total and costs no points.

**Pure and deterministic.** No model call, no database, no lookup from a classification to a number.
The inventory decides the denominator and the weights; the outcome records decide each finding's
status; this adds them up, enforces the invariants and words the result. Every surface (the report,
the studies list, the JSON and markdown exports) renders what this returns.
"""

from __future__ import annotations

import math

RUBRIC_VERSION = 1
RUBRIC_LABEL = "weighted rubric version 1"
TITLE = "Validation Scorecard"
EXPLANATION = (
    "The score measures agreement among assessed findings, with primary findings weighted twice as much as "
    "supporting findings. Scope counts assessed findings out of the total."
)

PRIMARY = "primary"
SUPPORTING = "supporting"
TECHNICAL = "technical"
CATEGORY_WEIGHTS = {PRIMARY: 2, SUPPORTING: 1, TECHNICAL: 0}
CATEGORY_LABELS = {
    PRIMARY: "Primary",
    SUPPORTING: "Supporting",
    TECHNICAL: "Technical prerequisite or descriptive check",
}

# One status per finding. The first two are conclusive and enter the assessed numerator; the other
# four keep the finding in the total and say why it has no conclusive result.
SUPPORTED = "supported"
DISCREPANCY = "discrepancy"
INCONCLUSIVE = "inconclusive"
BLOCKED = "blocked"
UNRESOLVED = "unresolved"
NOT_ATTEMPTED = "not_attempted"
ASSESSED_STATUSES = (SUPPORTED, DISCREPANCY)
UNASSESSED_STATUSES = (INCONCLUSIVE, BLOCKED, UNRESOLVED, NOT_ATTEMPTED)
STATUS_LABELS = {
    SUPPORTED: "Supported",
    DISCREPANCY: "Discrepancy",
    INCONCLUSIVE: "Attempted; result inconclusive",
    BLOCKED: "Blocked",
    UNRESOLVED: "Unresolved",
    NOT_ATTEMPTED: "Not attempted",
}

# The scorecard as a whole.
SCORED = "scored"
NOT_ASSESSED = "not_assessed"
NOT_ESTABLISHED = "not_established"
NOT_APPLICABLE = "not_applicable"
UNAVAILABLE = "unavailable"
SCORECARD_STATUS_LABELS = {
    SCORED: None,
    NOT_ASSESSED: None,
    NOT_ESTABLISHED: "Scope not established",
    NOT_APPLICABLE: "Not applicable",
    UNAVAILABLE: "Score unavailable for this historical report.",
}
IN_PROGRESS_LABEL = "In progress"

# plan_8_1 section 2.3, pending the owner's sign-off.
PROVISIONAL_SUFFIX = " (provisional)"
SCORE_PENDING_LABEL = "Score pending importance review"
IMPORTANCE_NOT_ESTABLISHED_LABEL = "Importance not established"

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


class ScorecardInvariantError(ValueError):
    """The inventory or the outcomes break a rule the scorecard depends on. Never shown as a number."""


def weight_for(category) -> int:
    """The fixed rubric weight of an importance category. Anything else has none."""
    if category not in CATEGORY_WEIGHTS:
        raise ValueError(f"{category!r} is not an importance category in {RUBRIC_LABEL}")
    return CATEGORY_WEIGHTS[category]


def display_score(
    *, supported_weight: int, assessed_weight: int, discrepant_count: int, supported_count: int
) -> int | None:
    """The whole-number score a person reads: nearest integer, a half rounding up.

    It never reads 100 while a finding is discrepant, and never 0 while one is supported, because both
    ends carry a meaning (every assessed finding agreed; none did) that rounding must not fake.
    """
    if assessed_weight <= 0:
        return None
    rounded = math.floor(100 * supported_weight / assessed_weight + 0.5)
    if discrepant_count:
        rounded = min(rounded, 99)
    if supported_count:
        rounded = max(rounded, 1)
    return rounded


def _empty(status: str, *, inventory: dict | None, reason: str | None, in_progress: bool) -> dict:
    return {
        "version": 1,
        "title": TITLE,
        "rubric_version": RUBRIC_VERSION,
        "rubric_label": RUBRIC_LABEL,
        "explanation": EXPLANATION,
        "status": status,
        "status_label": SCORECARD_STATUS_LABELS[status],
        "reason": reason,
        "in_progress": in_progress,
        "in_progress_label": IN_PROGRESS_LABEL if in_progress else None,
        "inventory_revision": (inventory or {}).get("revision"),
        "inventory_status": (inventory or {}).get("status"),
        "score": None,
        "display_score": None,
        "score_label": None,
        "scope_label": None,
        "supported_count": None,
        "discrepant_count": None,
        "assessed_count": None,
        "total_count": None,
        "supported_weight": None,
        "discrepant_weight": None,
        "assessed_weight": None,
        "primary_discrepancy_count": None,
        "primary_unassessed_count": None,
        "summary": None,
        "messages": [],
        "indicators": [],
        "assessed_items": [],
        "unassessed_items": [],
        "excluded_items": [],
        "unresolved_importance": [],
        # Set by the projection when the outcomes come from the record kept when the study concluded.
        "outcomes_recorded_at": None,
        # plan_8_1 section 1.3: whose limitation a card with no score is, when that is established.
        "cause": None,
        "cause_label": None,
        # plan_8_1 section 2.3: a scope whose N holds findings of unestablished importance, and a score
        # withheld because an assessed finding's weight is not established.
        "provisional": False,
        "provisional_note": None,
        "score_status_label": None,
    }


def _category(finding: dict) -> str:
    return ((finding.get("importance") or {}).get("category")) or ""


def _checked_weight(finding: dict) -> int:
    category = _category(finding)
    try:
        weight = weight_for(category)
    except ValueError as exc:
        raise ScorecardInvariantError(f"finding {finding.get('id')}: {exc}") from exc
    stored = (finding.get("importance") or {}).get("weight")
    if stored is not None and stored != weight:
        raise ScorecardInvariantError(
            f"finding {finding.get('id')} carries weight {stored!r}; {RUBRIC_LABEL} weighs a {category} finding {weight}"
        )
    return weight


def _validated(finding: dict) -> bool:
    """Whether a finding's importance is validated. A record written before plan_8_1 carries no
    separate statuses, and was only ever scored when every importance was validated."""
    return ((finding.get("importance") or {}).get("status") or "validated") == "validated"


def _item(finding: dict, outcome: dict, weight: int | None) -> dict:
    status = outcome.get("status") or NOT_ATTEMPTED
    if status not in STATUS_LABELS:
        raise ScorecardInvariantError(f"finding {finding.get('id')} has an unknown outcome status {status!r}")
    importance = finding.get("importance") or {}
    category = _category(finding)
    validated = _validated(finding)
    if validated:
        category_label = CATEGORY_LABELS[category]
    elif category in CATEGORY_LABELS:
        category_label = f"{IMPORTANCE_NOT_ESTABLISHED_LABEL} (proposed: {CATEGORY_LABELS[category]})"
    else:
        category_label = IMPORTANCE_NOT_ESTABLISHED_LABEL
    return {
        "finding_id": finding.get("id"),
        "description": finding.get("description"),
        "locator": finding.get("locator"),
        "category": category if validated else None,
        "proposed_category": None if validated else (category or None),
        "category_label": category_label,
        "importance_status": importance.get("status") or "validated",
        "importance_problem": None if validated else importance.get("problem"),
        "weight": weight,
        "rationale": importance.get("rationale"),
        "quote": importance.get("quote"),
        "importance_decided_by": importance.get("decided_by"),
        "claim_indices": list(finding.get("claim_indices") or []),
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": outcome.get("reason"),
        "cause": outcome.get("cause"),
        "cause_label": outcome.get("cause_label"),
        "assessment_method": outcome.get("assessment_method"),
        "method_label": outcome.get("method_label"),
        "supporting_checks": list(outcome.get("supporting_checks") or []),
        "subchecks": list(outcome.get("subchecks") or []),
        "supporting_evidence_ids": list(outcome.get("supporting_evidence_ids") or []),
        "comparison_criteria": outcome.get("comparison_criteria") or finding.get("criteria"),
        "analysis_selection_revision": outcome.get("analysis_selection_revision"),
    }


def _count_words(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _summary(items: list[dict]) -> str | None:
    """ "Four supporting findings supported; one primary finding discrepant." Generated from the items."""
    phrases: list[str] = []
    for statuses, verb in (
        ((SUPPORTED,), "supported"),
        ((DISCREPANCY,), "discrepant"),
        (UNASSESSED_STATUSES, "not assessed"),
    ):
        for category in (PRIMARY, SUPPORTING):
            n = sum(1 for i in items if i["category"] == category and i["status"] in statuses)
            if n:
                noun = "finding" if n == 1 else "findings"
                phrases.append(f"{_count_words(n)} {category} {noun} {verb}")
    if not phrases:
        return None
    sentence = "; ".join(phrases)
    return sentence[0].upper() + sentence[1:] + "."


def _unestablished_words(k: int) -> str:
    if k == 1:
        return "1 unassessed finding has no established importance; it could be primary."
    return f"{k} unassessed findings have no established importance; any could be primary."


def _messages(items: list[dict]) -> list[dict]:
    """What must never hide behind a high score or a truncated list: primary discrepancies and primary
    findings left unassessed. Context under the metrics, never a third metric.

    plan_8_1 section 2.3: the primary warning covers unassessed findings whose importance is not
    established, because any of them could be primary."""
    messages = [
        {
            "kind": "primary_discrepancy",
            "text": f"Primary discrepancy: {i['description']}",
            "findings": [i["finding_id"]],
        }
        for i in items
        if i["category"] == PRIMARY and i["status"] == DISCREPANCY
    ]
    unassessed = [i["finding_id"] for i in items if i["category"] == PRIMARY and i["status"] in UNASSESSED_STATUSES]
    if unassessed:
        text = "Primary finding remains unassessed" if len(unassessed) == 1 else "Primary findings remain unassessed"
        messages.append({"kind": "primary_unassessed", "text": text, "findings": unassessed})
    open_importance = [i["finding_id"] for i in items if i["weight"] is None and i["status"] in UNASSESSED_STATUSES]
    if open_importance:
        messages.append(
            {
                "kind": "importance_unestablished",
                "text": _unestablished_words(len(open_importance)),
                "findings": open_importance,
            }
        )
    return messages


def _indicators(items: list[dict]) -> list[dict]:
    """The primary facts the studies list shows beside the two metrics, so a high score cannot hide them."""
    discrepant = sum(1 for i in items if i["category"] == PRIMARY and i["status"] == DISCREPANCY)
    unassessed = sum(1 for i in items if i["category"] == PRIMARY and i["status"] in UNASSESSED_STATUSES)
    indicators = []
    if discrepant:
        text = "Primary discrepancy" if discrepant == 1 else "Primary discrepancies"
        indicators.append({"kind": "primary_discrepancy", "text": text})
    if unassessed:
        text = "Primary finding remains unassessed" if unassessed == 1 else "Primary findings remain unassessed"
        indicators.append({"kind": "primary_unassessed", "text": text})
    open_importance = sum(1 for i in items if i["weight"] is None and i["status"] in UNASSESSED_STATUSES)
    if open_importance:
        indicators.append({"kind": "importance_unestablished", "text": _unestablished_words(open_importance)})
    return indicators


def build_scorecard(inventory: dict | None, outcomes: dict[str, dict] | None, *, in_progress: bool = False) -> dict:
    """The scorecard for one validation study: the two metrics, their counts and weight sums, and the
    findings listed as assessed, not assessed, or not scored (weight zero)."""
    outcomes = outcomes or {}
    if not inventory:
        return _empty(UNAVAILABLE, inventory=None, reason=None, in_progress=in_progress)
    if inventory.get("status") not in ("established", "provisional", NOT_APPLICABLE):
        card = _empty(NOT_ESTABLISHED, inventory=inventory, reason=inventory.get("reason"), in_progress=in_progress)
        card["unresolved_importance"] = [
            {
                "finding_id": f.get("id"),
                "description": f.get("description"),
                "problem": (f.get("importance") or {}).get("problem"),
            }
            for f in inventory.get("findings") or []
            if isinstance(f, dict) and (f.get("importance") or {}).get("status") != "validated"
        ]
        return card

    findings = [f for f in inventory.get("findings") or [] if isinstance(f, dict)]
    ids = [f.get("id") for f in findings]
    if len(ids) != len(set(ids)):
        raise ScorecardInvariantError(f"the inventory repeats a finding id: {ids}")

    # plan_8_1 section 2.3: N counts every validated primary or supporting finding and every finding whose
    # importance is not validated, whatever category was proposed. Only a validated technical finding
    # leaves it, and only a validated importance gives an assessed finding weight.
    scoreable: list[dict] = []
    excluded: list[dict] = []
    for position, finding in enumerate(findings):
        weight = _checked_weight(finding) if _validated(finding) else None
        item = _item(finding, outcomes.get(finding.get("id")) or {}, weight)
        item["_position"] = position
        (excluded if weight == 0 else scoreable).append(item)

    if not scoreable:
        card = _empty(
            NOT_APPLICABLE,
            inventory=inventory,
            reason=inventory.get("reason")
            or "The reviewed inventory holds no primary or supporting computational finding to score.",
            in_progress=in_progress,
        )
        card.update(
            supported_count=0,
            discrepant_count=0,
            assessed_count=0,
            total_count=0,
            supported_weight=0,
            discrepant_weight=0,
            assessed_weight=0,
            primary_discrepancy_count=0,
            primary_unassessed_count=0,
            excluded_items=[_public(i) for i in excluded],
        )
        return card

    supported = [i for i in scoreable if i["status"] == SUPPORTED]
    discrepant = [i for i in scoreable if i["status"] == DISCREPANCY]
    assessed = supported + discrepant
    unassessed = [i for i in scoreable if i["status"] in UNASSESSED_STATUSES]
    supported_weight = sum(i["weight"] or 0 for i in supported)
    discrepant_weight = sum(i["weight"] or 0 for i in discrepant)
    assessed_weight = supported_weight + discrepant_weight
    open_importance = [i for i in scoreable if i["weight"] is None]
    withheld = any(i["weight"] is None for i in assessed)

    # The invariants plan_8 section 5 names. A breach is a defect, never a displayed number.
    if len(assessed) + len(unassessed) != len(scoreable):
        raise ScorecardInvariantError("a scoreable finding is in no scoring category, or in more than one")
    if len(assessed) > len(scoreable):
        raise ScorecardInvariantError("more findings assessed than the inventory holds")
    if assessed_weight != sum(i["weight"] or 0 for i in assessed):
        raise ScorecardInvariantError("the assessed weight is not the sum of the supported and discrepant weights")

    # plan_8_1 section 2.3: an assessed finding whose weight is not established withholds the score.
    score = 100 * supported_weight / assessed_weight if assessed_weight and not withheld else None
    shown = (
        None
        if withheld
        else display_score(
            supported_weight=supported_weight,
            assessed_weight=assessed_weight,
            discrepant_count=len(discrepant),
            supported_count=len(supported),
        )
    )
    ordered_assessed = sorted(
        assessed, key=lambda i: (0 if i["category"] == PRIMARY and i["status"] == DISCREPANCY else 1, i["_position"])
    )
    ordered_unassessed = sorted(unassessed, key=lambda i: (0 if i["category"] == PRIMARY else 1, i["_position"]))
    by_position = sorted(scoreable, key=lambda i: i["_position"])

    provisional = bool(open_importance)
    card = _empty(SCORED if assessed else NOT_ASSESSED, inventory=inventory, reason=None, in_progress=in_progress)
    card.update(
        score=score,
        display_score=shown,
        score_label=f"{shown} / 100" if shown is not None else None,
        score_status_label=SCORE_PENDING_LABEL if withheld else None,
        scope_label=f"{len(assessed)} / {len(scoreable)} assessed" + (PROVISIONAL_SUFFIX if provisional else ""),
        provisional=provisional,
        provisional_note=(
            f"The total includes {len(open_importance)} finding{'s' if len(open_importance) != 1 else ''} whose "
            "importance is not established; it falls by one for each that proves technical."
            if provisional
            else None
        ),
        supported_count=len(supported),
        discrepant_count=len(discrepant),
        assessed_count=len(assessed),
        total_count=len(scoreable),
        supported_weight=supported_weight,
        discrepant_weight=discrepant_weight,
        assessed_weight=assessed_weight,
        primary_discrepancy_count=sum(1 for i in discrepant if i["category"] == PRIMARY),
        primary_unassessed_count=sum(1 for i in unassessed if i["category"] == PRIMARY),
        summary=_summary(by_position),
        messages=_messages(by_position),
        indicators=_indicators(by_position),
        assessed_items=[_public(i) for i in ordered_assessed],
        unassessed_items=[_public(i) for i in ordered_unassessed],
        excluded_items=[_public(i) for i in excluded],
    )
    return card


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


_COMPACT_KEYS = (
    "status",
    "status_label",
    "score",
    "display_score",
    "score_label",
    "assessed_count",
    "total_count",
    "scope_label",
    "primary_discrepancy_count",
    "primary_unassessed_count",
    "indicators",
    "in_progress",
    "in_progress_label",
    "rubric_version",
    "inventory_revision",
    # plan_8_1 section 2.3: "provisional" reads the same wherever the scope appears.
    "provisional",
    "score_status_label",
    # plan_8_1 section 1.3: a failed read's list cell says what the report says.
    "reason",
    "cause",
    "cause_label",
)


def compact_scorecard(card: dict) -> dict:
    """The two metrics and the primary indicators, for the studies list. Cut from the same scorecard
    the report renders, so the two can never disagree."""
    return {key: card.get(key) for key in _COMPACT_KEYS}
