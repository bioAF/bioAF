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

from app.services.validation_rubric_v3 import (
    CRITERIA_BY_ID,
    CRITERION_EVIDENCE,
    FAILED,
    SECTION_TITLES,
    UNDETERMINED,
    VERIFIED,
)

# plan_8_6 sections 6 and 8: the request carries its section, title and acceptable evidence, and the
# contradiction test is per outcome. Both change what a cached judgment means, so the version moves.
CONTRACT_VERSION = 2
MODEL_ASSISTED = "model_assisted"

MET = "met"
UNMET = "unmet"
CANNOT_ESTABLISH = "cannot_establish"
JUDGMENT_OUTCOMES = (MET, UNMET, CANNOT_ESTABLISH)

_TO_RUBRIC = {MET: VERIFIED, UNMET: FAILED, CANNOT_ESTABLISH: UNDETERMINED}

# The assessor saying IT could not settle the question. Beside ANY outcome this is two answers in
# one, and picking the more favourable is exactly how an unsupported judgment becomes a point.
_UNDERCUTS_THE_ASSESSOR = re.compile(
    r"\b(?:cannot|can not|could not|can't|couldn't|unable to|not able to)\s+(?:be\s+)?"
    r"(?:establish|determine|tell|verify|confirm|assess|check|say|know)"
    r"|\b(?:may|might|could|possibly)\s+(?:well\s+)?(?:be|have been)\s+"
    r"(?:stated|specified|described|reported|given|present|documented|defined)\b"
    r"|\bit might be (?:stated|specified|described|reported|documented|defined)\b"
    r"|\b(?:was|were|is|are)\s+not\s+(?:supplied|provided|available|inspected|examined|retrieved|shown)\b"
    r"|\bnot\s+(?:been\s+)?(?:checked|inspected|examined|reviewed|retrieved)\b"
    r"|\b(?:evidence|passages?|packet)\s+(?:supplied|provided|here|in front of me)\s+(?:is|are)\s+"
    r"(?:too\s+)?(?:thin|insufficient|limited|incomplete)"
    r"|\belsewhere in the (?:paper|supplement|supplementary|manuscript)\b",
    re.I,
)

# The assessor saying THE PAPER does not state something. Beside `met` that contradicts the answer;
# beside `unmet` it IS the finding, and converting it to "untested" suppressed the ordinary way of
# reporting an absence in the one direction the rubric most needs to be trustworthy.
_ABSENCE_CLAIM = re.compile(
    r"\bnot (?:stated|specified|described|given|reported|defined|documented)\b"
    r"|\bdoes not (?:say|state|specify|describe|report|define|mention)\b"
    r"|\bdo not (?:say|state|specify|describe|report|define|mention)\b"
    r"|\bno (?:mention|statement|information|description|record)\b"
    r"|\bnowhere\b|\bis absent\b|\bare absent\b|\bomit(?:s|ted)?\b"
    r"|\bunclear\b|\bambiguous\b",
    re.I,
)

# A negative that rests on something the evidence POSITIVELY shows: two values that disagree, a
# method used where it does not apply, a step the supplied code does not perform. Section 8: such a
# finding is narrowly scoped and does not need the whole paper to have been inspected.
_POSITIVE_FINDING = re.compile(
    r"\bcontradict\w*|\bdisagree\w*|\bconflict\w*|\binconsisten\w*|\bmismatch\w*"
    r"|\bdiffers? from\b|\binstead of\b|\bwhile the\b|\bwhereas\b"
    r"|\bhas no replicate\b|\bno replicate to\b|\bsingle (?:sample|replicate)\b"
    r"|\binappropriate\w*|\bunsuitable\b|\bnot valid for\b|\bcannot support\b",
    re.I,
)


# plan_8_6 section 7: an obligation whose POSITIVE answer has to rest on a particular kind of
# evidence, and the words that say why when it does not.
#
# Study 65 returned verified for E2.B on rationales that named comparators and asserted their
# appropriateness. E2.A asks whether the controls and design choices are DESCRIBED; E2.B adds the
# reasoned evaluation of their adequacy, and that cannot be made from a list of comparators. The
# rule is structural, not another keyword list: a `met` answer has to cite a design fact bioAF read
# out of the paper, and where there are none the obligation is untested.
REQUIRES_CARRIED = {
    "E2.B": (
        "design",
        "E2.B is a reasoned evaluation of whether the design supports the comparison claimed, and "
        "this answer cites no design fact: the presence of a comparator does not establish the "
        "replication, the units compared, the group sizes or the selection the claim rests on",
    )
}


class JudgmentRefused(ValueError):
    """The request cannot be made; the words say why."""


_SYSTEM = (
    "You are judging ONE obligation of a validation rubric against the evidence below, and nothing else.\n\n"
    "Answer only about this obligation. Do not rate the paper, do not judge its quality, and do not "
    "produce any number other than the confidence field.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"outcome": "met | unmet | cannot_establish", "rationale": "one or two sentences", '
    '"citations": ["the id of every passage your rationale rests on"], "scope": "for unmet: the '
    'experiment, analysis or file the finding is about", "impact": "for unmet: the material '
    'consequence for a reader trying to repeat this", "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- cite the passages below by their id; a rationale that rests on something not listed here has no ground\n"
    "- the obligation's SCOPE and ACCEPTABLE EVIDENCE are stated with the question. Evidence of a kind the "
    "question excludes cannot establish it, however clearly it is written\n"
    "- answer `met` only if the listed evidence states what the obligation requires\n"
    "- answer `unmet` when the listed evidence establishes a defect in this obligation's scope. That is any "
    "of: the required item is absent from evidence that would carry it; a demonstrated contradiction, where "
    "two sources state different things about the same step; a method that is materially inappropriate for "
    "the data or design it is applied to; or a supplied procedure that does not reproduce what the paper "
    "reports. An absence is not the only kind\n"
    "- an `unmet` answer states its scope and its material consequence, and rests on cited passages. "
    "Do not answer `unmet` about sources you were not shown; say `cannot_establish` instead\n"
    "- answer `cannot_establish` for anything else, including evidence that is too thin to tell\n"
    "- do not hedge the answer you gave. If the evidence settles it, say so plainly; if it does not, "
    "answer `cannot_establish`. An answer whose reasoning withdraws it is recorded as settling nothing\n"
    "- a generic phrase such as 'standard procedures were used' does not state an actual method"
)


def _context_for(criterion_id: str) -> str:
    """Section 6: the scope and acceptable evidence a row's words lose when read on their own."""
    context = CRITERION_EVIDENCE.get(criterion_id) or {}
    lines = []
    if context.get("scope"):
        lines.append(f"Scope: {context['scope']}.")
    if context.get("accepts"):
        lines.append("Evidence that can answer it: " + "; ".join(context["accepts"]) + ".")
    if context.get("excludes"):
        lines.append("Evidence that cannot answer it: " + "; ".join(context["excludes"]) + ".")
    return "\n".join(lines)


def build_request(leaf: str, *, passages: list[dict] | None) -> dict:
    """The request for one obligation: its place in the rubric, its scope, and the evidence to judge it on.

    plan_8_6 section 6: the section name, the criterion title and BOTH halves of the criterion travel
    with the question. M1.B's "material settings" is a computational parameter because M1 is the
    Computational methods row about preprocessing, and an assessor shown only the sentence read it as
    laboratory materials and verified the obligation from culture media.
    """
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
    other = criterion.b if obligation == "A" else criterion.a
    payload = "\n\n".join(f"[{p['id']}] ({p.get('source') or 'unknown source'}) {p['text']}" for p in rows)
    section = SECTION_TITLES.get(criterion.section, criterion.section)
    question = [
        f"Rubric section {criterion.section}: {section}.",
        f"Criterion {criterion.id}: {criterion.title}.",
        _context_for(criterion.id),
        "",
        f"Obligation ({criterion.id} {obligation}): {statement}",
    ]
    if other:
        question.append(
            f"The other half of {criterion.id} ({criterion.id}.{'B' if obligation == 'A' else 'A'}), for context "
            f"only and NOT what you are answering: {other}"
        )
    return {
        "leaf": leaf,
        "criterion": criterion.id,
        "obligation": statement,
        "title": criterion.title,
        "section": section,
        "scope": (CRITERION_EVIDENCE.get(criterion.id) or {}).get("scope"),
        "system": _SYSTEM,
        "payload": "\n".join(p for p in question if p is not None) + f"\n\nEvidence:\n{payload}",
        "evidence": [{"id": p["id"], "source": p.get("source")} for p in rows],
        "schema": {"outcome": list(JUDGMENT_OUTCOMES), "citations": "ids from the evidence above"},
        "contract_version": CONTRACT_VERSION,
    }


def contradicts_itself(outcome: str, rationale: str) -> bool:
    """Whether the rationale withdraws the answer it accompanies. Section 8: this is PER OUTCOME.

    For ``met``, a rationale saying the paper does not state it, or that the assessor could not tell,
    is two answers in one. For ``unmet``, "the paper does not state X" IS the finding; only a
    rationale that undercuts its own negative ("it may be stated elsewhere", "this could not be
    checked") is a contradiction.
    """
    text = rationale or ""
    if _UNDERCUTS_THE_ASSESSOR.search(text):
        return True
    return outcome == MET and bool(_ABSENCE_CLAIM.search(text))


def needs_coverage(rationale: str) -> bool:
    """Whether this negative is an ABSENCE finding, which rests on what was inspected.

    Section 8: a negative resting on positive evidence (two values that disagree, a method applied
    where it does not hold, a step the supplied code does not perform) is narrowly scoped and is not
    a claim about sources nobody opened. An absence finding is a claim about them.
    """
    text = rationale or ""
    if _POSITIVE_FINDING.search(text):
        return False
    return bool(_ABSENCE_CLAIM.search(text))


def coverage_supports_absence(coverage: dict | None) -> tuple[bool, str]:
    """Whether the evidence record establishes that the sources an absence is about were inspected.

    ``None`` means the caller made no coverage claim, and this makes none either: the judgment
    records that its basis is unrecorded rather than inventing one. A production caller supplies the
    record ``validation_evidence_packets`` builds.
    """
    if coverage is None:
        return True, ""
    if not isinstance(coverage, dict):
        return False, "the coverage of this obligation's evidence was not recorded in a form bioAF can read"
    if coverage.get("truncated"):
        return False, (
            "the evidence packet was truncated by its budget, and a packet that did not carry every "
            "relevant passage cannot establish that the paper omits something"
        )
    if not coverage.get("sufficient"):
        reason = str(coverage.get("reason") or "").strip()
        return False, (
            "bioAF did not inspect the sources this absence would be about"
            + (f" ({reason})" if reason else "")
            + "; not retrieving a source is not the same finding as the paper omitting a method"
        )
    return True, ""


def judgment_from(
    leaf: str,
    answer: dict | None,
    *,
    passages: list[dict] | None,
    model: str | None = None,
    coverage: dict | None = None,
) -> dict:
    """One model answer, checked and turned into a rubric outcome. Never raises.

    A judge failure affects its own obligation and nothing else: an absent or unusable answer is one
    undetermined leaf with the next action on it.

    ``coverage`` is the section 3 record of what this obligation's evidence packet actually carried.
    An absence finding is accepted only where it establishes that the relevant sources were inspected.
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
    if contradicts_itself(outcome, rationale):
        # Two answers in one. Section 5: record the conflict, seek a bounded clarification, and never
        # average contradictory judgments into a point.
        return _open(
            f"the assessor answered {outcome} and its reasoning withdraws that answer",
            next_action="ask again for this obligation alone, or record a human review with its evidence",
            assessor=assessor,
            confidence=answer.get("confidence"),
            conflict={"outcome": outcome, "rationale": rationale},
            coverage=coverage,
        )
    required = REQUIRES_CARRIED.get(leaf)
    if outcome == MET and required is not None:
        carried = {
            str(p.get("id"))
            for p in passages or []
            if isinstance(p, dict) and str(p.get("carries") or "") == required[0]
        }
        if not (carried & set(citations)):
            return _open(
                required[1],
                next_action="cite the design facts this evaluation rests on, or record that bioAF read none",
                assessor=assessor,
                confidence=answer.get("confidence"),
                withheld={"outcome": outcome, "rationale": rationale},
                coverage=coverage,
            )
    if outcome == UNMET and needs_coverage(rationale):
        # plan_8_6 section 8: an absence is a claim about what was looked at. A source bioAF never
        # retrieved cannot establish that the authors omitted anything.
        supported, why = coverage_supports_absence(coverage)
        if not supported:
            return _open(
                why,
                next_action="retrieve and inspect the sources this obligation is about, then ask again",
                assessor=assessor,
                confidence=answer.get("confidence"),
                withheld={"outcome": outcome, "rationale": rationale},
                coverage=coverage,
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
        # What the packet this was judged on actually carried, so a reader can see what an absence
        # finding rests on. ``None`` says the caller recorded none.
        "coverage": coverage,
    }
    if outcome == UNMET:
        found["impact"] = str(answer.get("impact") or "").strip() or (
            "what the obligation requires is absent from the evidence inspected"
        )
        found["finding_scope"] = str(answer.get("scope") or "").strip() or found["scope"]
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
