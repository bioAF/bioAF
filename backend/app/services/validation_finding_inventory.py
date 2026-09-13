"""plan_8 section 2: the reviewed inventory of a paper's distinct computational findings.

The scorecard's denominator is this inventory, never the number of files, pipeline outputs, extracted
numbers or successful comparisons. Each finding groups the claims (the comparison targets) that
together establish one result, so several metrics or a repeated test of one finding are one
opportunity to earn agreement, never several.

**A model proposes, the rubric validates.** The reading proposes the grouping and each finding's
importance category, with a rationale and the paper's own words for it, before anything is measured.
This module checks the proposal against the fixed rubric (weighted rubric version 1):

- the category is one of primary, supporting, technical; its weight is the rubric's, never a number
  the model supplied;
- a rationale is given, and a scoreable finding's quote is found in the paper's text;
- every claim sits in exactly one finding.

Anything the rubric cannot validate leaves the inventory ``unresolved``: the scorecard then shows
"Scope not established" with the reason, rather than scoring an inventory whose weights or
denominator were defaulted. Nothing is omitted silently and nothing is weighted by whether it passed.

**Versioned.** A justified correction (``revise_importance``) is a new revision with its reason and
who made it; the revision it replaces is kept whole in ``history``, with the scorecard it produced, so
a historical score is never rewritten.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from app.services.validation_scorecard import (
    CATEGORY_WEIGHTS,
    PRIMARY,
    RUBRIC_LABEL,
    RUBRIC_VERSION,
    SUPPORTING,
    TECHNICAL,
)

ESTABLISHED = "established"
UNRESOLVED = "unresolved"
NOT_APPLICABLE = "not_applicable"
# plan_8_1 section 2.3: membership is established and some finding's importance is not validated. The
# scope stands, marked provisional; it never satisfies anything that requires an established one.
PROVISIONAL = "provisional"

VALIDATED = "validated"
# plan_8_1 section 2.3: a valid category whose support failed (no rationale, a quote not found, a weight
# of the model's own), and no valid category at all.
PROPOSED = "proposed"
UNKNOWN = "unknown"

MEMBERSHIP_ESTABLISHED = "established"
MEMBERSHIP_NOT_ESTABLISHED = "not_established"

# The category names a proposal may use: the rubric's short names and its full wording.
_CATEGORY_NAMES = {
    "primary": PRIMARY,
    "primary finding": PRIMARY,
    "supporting": SUPPORTING,
    "supporting finding": SUPPORTING,
    "technical": TECHNICAL,
    "technical prerequisite": TECHNICAL,
    "descriptive check": TECHNICAL,
    "technical prerequisite or descriptive check": TECHNICAL,
}

# rubric version 1's finding-level rule, recorded on every finding before any result exists. Mixed or
# incomplete subchecks are inconclusive; there is no partial credit.
CRITERIA = {
    "rule": "all_required_subchecks",
    "words": (
        "Supported when every required claim is supported by a valid independent assessment; a discrepancy "
        "when every required claim was conclusively assessed and none of them supported; otherwise not "
        "conclusively assessed."
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _category_of(value) -> str | None:
    return _CATEGORY_NAMES.get(_text(value).lower()) if isinstance(value, str) else None


def _claim_numbers(indices) -> str:
    """Claims as a person counts them, from one."""
    numbers = [str(i + 1) for i in indices]
    noun = "claim" if len(numbers) == 1 else "claims"
    return f"{noun} {', '.join(numbers)}"


def _only(values):
    distinct = {v for v in values if v is not None}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _importance(raw: dict, *, decided_by: dict, full_text: str | None) -> dict:
    """The proposed importance, checked against the rubric. ``problem`` says why it is not validated."""
    problems: list[str] = []
    proposed = raw.get("importance") if raw.get("importance") is not None else raw.get("category")
    category = _category_of(proposed)
    weight = CATEGORY_WEIGHTS.get(category) if category else None
    if category is None:
        problems.append(f"its category {proposed!r} is not one of primary, supporting or technical in {RUBRIC_LABEL}")
    offered = raw.get("weight")
    if offered is not None and (category is None or offered != weight):
        problems.append(f"a numeric weight of {offered!r} was proposed; {RUBRIC_LABEL} fixes the weight by category")
    rationale = _text(raw.get("rationale"))
    if not rationale:
        problems.append("no rationale was given for its importance")
    quote = _text(raw.get("quote")) or None
    if category in (PRIMARY, SUPPORTING):
        from app.services.validation_passages import quote_in_text

        if not quote:
            problems.append("no quote from the paper supports its importance")
        elif not full_text or not quote_in_text(quote, full_text):
            problems.append("its quote is not in the paper's text")
    return {
        "category": category,
        "weight": weight,
        "rationale": rationale or None,
        "quote": quote,
        "status": VALIDATED if not problems else (PROPOSED if category else UNKNOWN),
        "problem": "; ".join(problems) or None,
        "decided_by": decided_by.get("kind") or "model",
        "model": decided_by.get("model"),
        "validated_by": RUBRIC_LABEL,
    }


def _sentence(reasons: list[str]) -> str | None:
    if not reasons:
        return None
    sentence = "; ".join(reasons)
    return sentence[0].upper() + sentence[1:] + "."


def _settle(inventory: dict) -> dict:
    """The inventory's status and reason, from its findings' membership and importance.

    plan_8_1 section 2.3: membership decides whether there is a denominator at all; importance only
    whether the scope is provisional. Unresolved membership is "Scope not established"; importance not
    validated is a provisional scope, never a blank one.
    """
    membership = []
    if inventory.get("_unreadable"):
        membership.append(inventory.pop("_unreadable"))
    for finding in inventory["findings"]:
        finding.setdefault("membership", {"status": MEMBERSHIP_ESTABLISHED, "problems": []})
        if finding["membership"]["status"] != MEMBERSHIP_ESTABLISHED:
            membership.append(f"{finding['id']}: {'; '.join(finding['membership']['problems'])}")
    unplaced = inventory.get("unplaced_claims") or []
    if unplaced:
        membership.append(f"{_claim_numbers(unplaced)} {'belongs' if len(unplaced) == 1 else 'belong'} to no finding")
    inventory["membership"] = {
        "status": MEMBERSHIP_NOT_ESTABLISHED if membership else MEMBERSHIP_ESTABLISHED,
        "reason": _sentence(membership),
    }
    open_findings = [f for f in inventory["findings"] if f["importance"]["status"] != VALIDATED]
    importance = [
        f"the importance of {f['id']} is not established: {f['importance']['problem']}" for f in open_findings
    ]
    if membership:
        inventory["status"] = UNRESOLVED
        inventory["reason"] = _sentence(membership + importance)
    elif open_findings:
        inventory["status"] = PROVISIONAL
        inventory["reason"] = _sentence(importance)
    elif not any(f["importance"]["weight"] for f in inventory["findings"]):
        inventory["status"] = NOT_APPLICABLE
        inventory["reason"] = (
            "Every finding in the reviewed inventory is a technical prerequisite or descriptive check."
            if inventory["findings"]
            else "The paper states no computational finding bioAF extracted."
        )
    else:
        inventory["status"] = ESTABLISHED
        inventory["reason"] = None
    return inventory


def _authority() -> dict:
    from app.services.validation_rubric_v2 import AUTHORITY

    return {"rubric_version": RUBRIC_VERSION, **AUTHORITY}


def inventory_from_proposal(
    raw,
    *,
    targets: list[dict],
    full_text: str | None,
    decided_by: dict,
    claim_targets: dict[int, int] | None = None,
    parse_failure: bool = False,
) -> dict:
    """Revision 1 of the inventory, from the reading's proposed ``findings``.

    ``claim_targets`` maps a claim's position in the reading to its position among the kept targets
    (a claim with neither a metric nor the paper's words is not kept); identity when omitted.
    """
    mapping = claim_targets if claim_targets is not None else {i: i for i in range(len(targets))}
    inventory: dict = {
        "rubric_version": RUBRIC_VERSION,
        "revision": 1,
        "status": None,
        "reason": None,
        "findings": [],
        "unplaced_claims": [],
        "decided_by": dict(decided_by),
        "at": _now(),
        "history": [],
        # plan_8_1 section 4.3: the authority rules, declared before any result can reshape them.
        "authority": _authority(),
    }
    if parse_failure:
        inventory["_unreadable"] = "the paper could not be read into findings"
    elif not isinstance(raw, list):
        inventory["_unreadable"] = "the reading proposed no findings for the paper's claims"

    proposals = [r for r in raw or [] if isinstance(r, dict)] if isinstance(raw, list) else []
    placed: dict[int, str] = {}
    prerequisites: list[list] = []
    for position, proposal in enumerate(proposals):
        finding_id = f"F{position + 1}"
        importance = _importance(proposal, decided_by=decided_by, full_text=full_text)
        claims: list[int] = []
        problems: list[str] = []
        for index in proposal.get("claim_indices") or []:
            if isinstance(index, bool) or not isinstance(index, int) or index not in mapping:
                continue
            target = mapping[index]
            if target in placed:
                problems.append(f"{_claim_numbers([target])} is already in {placed[target]}")
                continue
            if target not in claims:
                claims.append(target)
        for target in claims:
            placed[target] = finding_id
        if not claims and not problems:
            problems.append("it holds none of the paper's claims")
        # plan_8_1 section 2.3: where a finding's claims sit is its membership, recorded apart from its
        # importance, so a misplaced claim never makes a validated importance look unvalidated.
        membership = {
            "status": MEMBERSHIP_NOT_ESTABLISHED if problems else MEMBERSHIP_ESTABLISHED,
            "problems": problems,
        }
        members = [targets[i] for i in claims if 0 <= i < len(targets)]
        locators = list(
            dict.fromkeys(_text(t.get("source_locator")) for t in members if _text(t.get("source_locator")))
        )
        inventory["findings"].append(
            {
                "id": finding_id,
                "description": _text(proposal.get("description"))
                or (_text(members[0].get("claim_text")) if members else None),
                "locator": _text(proposal.get("locator")) or "; ".join(locators) or None,
                "experiment_id": _only(t.get("reported_experiment_id") for t in members),
                "contrast_index": _only(
                    t.get("contrast_index") for t in members if isinstance(t.get("contrast_index"), int)
                ),
                "claim_indices": claims,
                "required": list(claims),
                "prerequisite_for": [],
                "membership": membership,
                "importance": importance,
                "criteria": dict(CRITERIA),
            }
        )
        prerequisites.append(proposal.get("prerequisite_for") or [])

    count = len(inventory["findings"])
    for finding, wanted in zip(inventory["findings"], prerequisites):
        if finding["importance"]["category"] != TECHNICAL:
            continue
        finding["prerequisite_for"] = [
            f"F{i + 1}"
            for i in wanted
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < count and f"F{i + 1}" != finding["id"]
        ]
    inventory["unplaced_claims"] = [i for i in range(len(targets)) if i not in placed]
    return _settle(inventory)


def revise_importance(
    inventory: dict,
    finding_id: str,
    *,
    category: str,
    rationale: str,
    reason: str,
    decided_by: dict,
    quote: str | None = None,
    snapshot: dict | None = None,
) -> dict:
    """A justified correction to one finding's importance, as a new revision.

    The revision it replaces is kept whole in ``history`` beside the scorecard it produced
    (``snapshot``), so the historical score is never rewritten. A weight-only correction reruns no
    analysis: the outcomes stand, and only the weights they are summed under change.
    """
    if category not in CATEGORY_WEIGHTS:
        raise ValueError(f"{category!r} is not an importance category in {RUBRIC_LABEL}")
    if not _text(rationale):
        raise ValueError("a correction to importance needs a rationale")
    if not _text(reason):
        raise ValueError("a correction to importance needs the reason it was made")
    if not any(f.get("id") == finding_id for f in inventory.get("findings") or []):
        raise ValueError(f"the inventory has no finding {finding_id}")

    revised = copy.deepcopy(inventory)
    previous = {k: copy.deepcopy(v) for k, v in inventory.items() if k != "history"}
    previous.update(superseded_at=_now(), superseded_reason=_text(reason), scorecard=snapshot)
    revised["history"] = list(copy.deepcopy(inventory.get("history") or [])) + [previous]
    for finding in revised["findings"]:
        if finding["id"] != finding_id:
            continue
        finding["importance"] = {
            "category": category,
            "weight": CATEGORY_WEIGHTS[category],
            "rationale": _text(rationale),
            "quote": _text(quote) or None,
            "status": VALIDATED,
            "problem": None,
            "decided_by": decided_by.get("kind") or "person",
            "user_id": decided_by.get("user_id"),
            "validated_by": RUBRIC_LABEL,
        }
    revised["revision"] = int(inventory.get("revision") or 1) + 1
    revised["revised"] = {
        "finding_id": finding_id,
        "reason": _text(reason),
        "decided_by": dict(decided_by),
        "at": _now(),
    }
    return _settle(revised)
