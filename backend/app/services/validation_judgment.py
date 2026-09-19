"""plan_8_4 section 5: what bioAF may ask a model about one rubric obligation, and what it does with the answer.

A model is asked about ONE criterion-specific obligation, with the evidence it is to be judged on in
front of it. It is never asked for a holistic rating of the paper and never for a number: the assessor
establishes a grounded outcome and the scorer adds the weights, and a model that produced the number
would be producing the score.

What comes back is checked before it becomes a point:

- the outcome has to be one the contract declares;
- every citation has to be a passage that was actually supplied. Recollection is not evidence, and a
  citation bioAF cannot resolve is a judgment with no ground;
- a judgment that asserts an outcome and also says it could not be established is CONTRADICTORY, and a
  contradiction is recorded and left undetermined rather than averaged into something;
- confidence is recorded and is never a multiplier. A leaf is verified or it is not.

Quote matching alone does not prove that a quoted passage supports the assessment, so a resolvable
citation is a floor, not a proof. Nothing here calls a model: this is the contract and its checking.
"""

from __future__ import annotations

import re

from app.services.validation_rubric_v3 import CRITERIA_BY_ID, FAILED, UNDETERMINED, VERIFIED

CONTRACT_VERSION = 1
MODEL_ASSISTED = "model_assisted"

MET = "met"
UNMET = "unmet"
CANNOT_ESTABLISH = "cannot_establish"
JUDGMENT_OUTCOMES = (MET, UNMET, CANNOT_ESTABLISH)

_TO_RUBRIC = {MET: VERIFIED, UNMET: FAILED, CANNOT_ESTABLISH: UNDETERMINED}

# A rationale that hedges its own outcome away. "met" beside "the methods do not say" is two answers,
# and picking the more favourable one is exactly how an unsupported judgment becomes a point.
_HEDGE = re.compile(
    r"\b(?:cannot|could not|can't|couldn't|unable to)\s+(?:be\s+)?(?:establish|determine|tell|verify|confirm|assess)"
    r"|\bnot (?:stated|specified|described|given|reported)\b"
    r"|\bdoes not say\b|\bno (?:mention|statement|information)\b|\bunclear\b|\bambiguous\b",
    re.I,
)


class JudgmentRefused(ValueError):
    """The request cannot be made; the words say why."""


_SYSTEM = (
    "You are judging ONE obligation of a validation rubric against the evidence below, and nothing else.\n\n"
    "Answer only about this obligation. Do not rate the paper, do not judge its quality, and do not "
    "produce any number other than the confidence field.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"outcome": "met | unmet | cannot_establish", "rationale": "one or two sentences", '
    '"citations": ["the id of every passage your rationale rests on"], "impact": "for unmet: what it '
    'costs a reader trying to repeat this", "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- cite the passages below by their id; a rationale that rests on something not listed here has no ground\n"
    "- answer `met` only if the listed evidence states what the obligation requires\n"
    "- answer `unmet` only if the listed evidence establishes that what the obligation requires is absent\n"
    "- answer `cannot_establish` for anything else, including evidence that is too thin to tell\n"
    "- a generic phrase such as 'standard procedures were used' does not state an actual method"
)


def build_request(leaf: str, *, passages: list[dict] | None) -> dict:
    """The request for one obligation: the obligation's own words, and the evidence to judge it on."""
    criterion_id, _, obligation = str(leaf).partition(".")
    criterion = CRITERIA_BY_ID.get(criterion_id)
    if criterion is None or obligation not in ("A", "B"):
        raise JudgmentRefused(f"{leaf} is not an obligation rubric v3 declares")
    rows = [p for p in passages or [] if isinstance(p, dict) and p.get("id") and str(p.get("text") or "").strip()]
    if not rows:
        raise JudgmentRefused(
            f"{leaf} cannot be judged with no evidence in front of the assessor; a judgment made from "
            "recollection is not an assessment"
        )
    statement = criterion.a if obligation == "A" else criterion.b
    payload = "\n\n".join(f"[{p['id']}] ({p.get('source') or 'unknown source'}) {p['text']}" for p in rows)
    return {
        "leaf": leaf,
        "criterion": criterion.id,
        "obligation": statement,
        "title": criterion.title,
        "system": _SYSTEM,
        "payload": f"Obligation ({criterion.id} {obligation}): {statement}\n\nEvidence:\n{payload}",
        "evidence": [{"id": p["id"], "source": p.get("source")} for p in rows],
        "schema": {"outcome": list(JUDGMENT_OUTCOMES), "citations": "ids from the evidence above"},
        "contract_version": CONTRACT_VERSION,
    }


def judgment_from(leaf: str, answer: dict | None, *, passages: list[dict] | None, model: str | None = None) -> dict:
    """One model answer, checked and turned into a rubric outcome. Never raises.

    A judge failure affects its own obligation and nothing else: an absent or unusable answer is one
    undetermined leaf with the next action on it.
    """
    supplied = {str(p.get("id")) for p in passages or [] if isinstance(p, dict) and p.get("id")}
    assessor = {"model": model, "contract_version": CONTRACT_VERSION, "method": MODEL_ASSISTED}
    if not isinstance(answer, dict):
        return _open(
            "the assessor returned no usable judgment for this obligation",
            next_action="ask again for this obligation alone",
            assessor=assessor,
        )
    outcome = str(answer.get("outcome") or "").strip().lower()
    rationale = str(answer.get("rationale") or "").strip()
    citations = [str(c) for c in answer.get("citations") or []]
    if outcome not in JUDGMENT_OUTCOMES:
        return _open(
            f"the assessor answered {outcome or 'nothing'}, which is not one of the outcomes this obligation takes",
            next_action="ask again for this obligation alone",
            assessor=assessor,
        )
    if outcome == CANNOT_ESTABLISH:
        return _open(
            rationale or "the assessor could not establish this obligation from the evidence supplied",
            next_action="supply the passages that would settle it, or record that none exist",
            assessor=assessor,
            confidence=answer.get("confidence"),
        )
    unresolvable = [c for c in citations if c not in supplied]
    if not citations or unresolvable:
        return _open(
            (
                f"the assessor cited {', '.join(unresolvable)}, which was not among the evidence supplied"
                if unresolvable
                else "the assessor cited nothing, so its answer rests on no evidence bioAF can check"
            ),
            next_action="ask again, requiring a citation from the evidence supplied",
            assessor=assessor,
            confidence=answer.get("confidence"),
        )
    if _HEDGE.search(rationale):
        # Two answers in one. Section 5: record the conflict, seek a bounded clarification, and never
        # average contradictory judgments into a point.
        return _open(
            f"the assessor answered {outcome} and its reasoning says the evidence does not establish it",
            next_action="ask again for this obligation alone, or record a human review with its evidence",
            assessor=assessor,
            confidence=answer.get("confidence"),
            conflict={"outcome": outcome, "rationale": rationale},
        )
    found = {
        "outcome": _TO_RUBRIC[outcome],
        "rationale": rationale or f"the assessor judged this obligation {outcome}",
        "scope": f"{len(supplied)} supplied passages",
        "method": MODEL_ASSISTED,
        "evidence": {"citations": citations},
        "assessor": assessor,
        # Recorded, and never a multiplier: a leaf is verified or it is not (section 4).
        "confidence": answer.get("confidence"),
    }
    if outcome == UNMET:
        found["impact"] = str(answer.get("impact") or "").strip() or (
            "what the obligation requires is absent from the evidence inspected"
        )
    return found


def _open(rationale: str, *, next_action: str, assessor: dict, **extra) -> dict:
    return {
        "outcome": UNDETERMINED,
        "rationale": rationale,
        "scope": "the evidence supplied to the assessor",
        "method": MODEL_ASSISTED,
        "next_action": next_action,
        "assessor": assessor,
        **extra,
    }
