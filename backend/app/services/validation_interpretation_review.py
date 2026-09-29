"""plan_8_7 stage 2: does the result support the conclusion, in the design actually examined?

The owner's September 21 assessment:

    "Interpretation review is limited to execution-related numbers and short context."

`signal_assessment` asks whether a paper's number could be noise, and what could explain a
divergence. Both are gated on an arm that ran code and produced a number to set against the paper's,
which is right for what they are: they reason FROM a comparison. The consequence is that a paper
whose data cannot be acquired, whose assay bioAF has no adapter for, or whose code will not resolve
gets no interpretation review at all, however plainly its own design settles the question.

This review is about the inference, not the number. Given the conclusion the paper states, the
procedure and design it rests on, the results as reported, the sample and replication records and
the relevant code, does the result support it within the design, uncertainty and population actually
examined?

- **it works without a run.** A completed comparison is extra context where one exists. Its absence
  is bioAF's limitation and is never evidence that an effect disappeared.
- **three conclusions stay apart.** Reproducing the authors' output, independently supporting a
  result and supporting the authors' interpretation are three different things (section 3), so the
  reproduction state travels beside the outcome and never becomes it.
- **it invents no evidence.** Every answer cites the context it was given; a citation bioAF did not
  supply leaves the conclusion unresolved, exactly as `validation_judgment` treats one.
- **one conclusion per request**, so a failure affects its own conclusion and nothing else.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, decide_with_recovery

logger = logging.getLogger("bioaf.validation_interpretation_review")

# 1: the first version of the review that reads a conclusion without waiting for an execution.
REVIEW_VERSION = 1

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
UNRESOLVED = "unresolved"
OUTCOMES = (SUPPORTED, CONTRADICTED, UNRESOLVED)
MODEL_ASSISTED = "model_assisted"

# What one assessment may spend on interpretation. plan_8_7 stage 0 declares the aggregate; this is
# the share of it this review may take, and a paper with more conclusions defers the rest and says so
# rather than multiplying the ceiling.
MAX_CONCLUSIONS = 12
MAX_CONTEXT_CHARS = 40_000

INTENT = "assessing whether a stated conclusion is supported by the result and design it rests on"

_SYSTEM = (
    "You are assessing ONE stated conclusion of a scientific paper, and nothing else. The question is "
    "whether the result supports that conclusion WITHIN the design, the uncertainty and the population "
    "actually examined.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"conclusion_id": "the id you were given", "outcome": "supported | contradicted | unresolved", '
    '"rationale": "one or two sentences", "inferential_step": "the step from result to conclusion that '
    'is at issue", "impact": "what a reader cannot rely on if this step does not hold", '
    '"citations": ["the id of every piece of context your answer rests on"], "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- cite the context below by its id. An answer resting on something not listed here has no ground.\n"
    "- `supported` means the result, as reported, does support the conclusion for the population and "
    "design examined. `contradicted` means a specific inferential step does not hold and you can point "
    "at the evidence. `unresolved` is a real answer and is often the right one.\n"
    "- you are NOT judging whether the finding is true, whether the experiment was worth doing, or "
    "whether the authors acted in good faith. Do not infer misconduct from a discrepancy.\n"
    "- do not produce any number other than the confidence field, and never state a measured value "
    "that is not in the context you were given.\n"
    "- where no independent reproduction is listed, that is bioAF's limitation. Do NOT treat it as "
    "evidence that the effect is absent, smaller, or would not replicate.\n"
    "- a conclusion can be over-generalised rather than wrong: say which population or condition the "
    "evidence actually reaches.\n"
    "- `inferential_step` names the reasoning at issue, such as treating two parental lines as "
    "replicates, or reading a correlation as a mechanism."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(conclusion_id: str):
    def check(data: dict) -> list[str]:
        problems: list[str] = []
        data = data or {}
        if str(data.get("conclusion_id") or "").strip() != conclusion_id:
            problems.append(f"`conclusion_id` must be exactly {conclusion_id}")
        if str(data.get("outcome") or "").strip().lower() not in OUTCOMES:
            problems.append("`outcome` must be exactly one of " + ", ".join(OUTCOMES))
        if str(data.get("outcome") or "").strip().lower() in (SUPPORTED, CONTRADICTED) and not data.get("citations"):
            problems.append("an answer of supported or contradicted cites the ids of the context it rests on")
        if not str(data.get("rationale") or "").strip():
            problems.append("`rationale` says in one or two sentences why the answer is what it is")
        return problems

    return check


def _reproduction_of(comparisons: list[dict]) -> str | None:
    """What an authors-code or independent comparison established, or None where none ran.

    It is recorded BESIDE the interpretation, never as it: agreement with the authors' output does not
    establish that the authors' interpretation follows from it.
    """
    rows = [c for c in comparisons or [] if isinstance(c, dict)]
    if not rows:
        return None
    if any(c.get("agrees") is True for c in rows):
        return "agreed"
    if any(c.get("agrees") is False for c in rows):
        return "disagreed"
    return "inconclusive"


def _payload(conclusion: dict, context: list[dict], comparisons: list[dict]) -> str:
    rows: list[str] = []
    spent = 0
    for row in context:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        if spent + len(text) > MAX_CONTEXT_CHARS:
            break
        spent += len(text)
        rows.append(f"[{row.get('id')}] ({row.get('source') or 'the paper'}) {text}")
    measured = [
        f"[{c.get('id')}] ({c.get('method') or 'independent reanalysis'}) {c.get('metric') or 'the reported result'}: the paper reports {c.get('paper')}, "
        f"bioAF's run produced {c.get('ours')}"
        + (
            ""
            if c.get("agrees") is None
            else f" ({'within' if c.get('agrees') else 'outside'} the declared comparison contract)"
        )
        for c in comparisons or []
        if isinstance(c, dict)
    ]
    return (
        f"Conclusion id: {conclusion.get('id')}\n"
        f"The paper concludes: {conclusion.get('statement')}\n"
        + (f"As stated in the paper: {conclusion['passage']}\n" if conclusion.get("passage") else "")
        + "\nContext:\n"
        + ("\n\n".join(rows) if rows else "bioAF holds no further context for this conclusion.")
        + "\n\nResult comparisons bioAF completed (author code and independent reanalysis are distinguished):\n"
        + (
            "\n".join(measured)
            if measured
            else "None. bioAF has not reproduced any result for this paper, which says nothing about "
            "whether the effect is real."
        )
    )


async def review_interpretations(
    *,
    conclusions: list[dict] | None,
    context: list[dict] | None,
    comparisons: list[dict] | None = None,
    client,
    model: str,
    api_key: str | None,
) -> dict:
    """``{"reviews", "scope", "deferred", "asked", "reason", ...}``: one review per conclusion. Never raises.

    ``conclusions`` are ``{"id", "statement", "passage"}``. ``context`` are the design, methods,
    results, sample and code rows the review may cite, each ``{"id", "source", "text"}``.
    """
    rows = [c for c in conclusions or [] if isinstance(c, dict) and str(c.get("id") or "").strip()]
    context = [c for c in context or [] if isinstance(c, dict)]
    comparisons = [c for c in comparisons or [] if isinstance(c, dict)]
    reproduction = _reproduction_of(comparisons)
    scope = f"{len(rows)} stated conclusion{'' if len(rows) == 1 else 's'}, " + (
        f"with {len(comparisons)} completed comparison(s)" if comparisons else "with no reproduction performed"
    )
    asked = {"requests": 0}
    if not rows:
        return _record({}, scope=scope, asked=asked, reason=None, reproduction=reproduction)
    if client is None or not model:
        reason = "no language model is configured for this organisation, so no interpretation was reviewed"
        return _record(
            {c["id"]: _open(reason, reproduction) for c in rows},
            scope=scope,
            asked=asked,
            reason=reason,
            reproduction=reproduction,
        )
    supplied = {str(c.get("id")) for c in context} | {str(c.get("id")) for c in comparisons}
    reviews: dict[str, dict] = {}
    for conclusion in rows[:MAX_CONCLUSIONS]:
        asked["requests"] += 1
        reviews[conclusion["id"]] = await _review(
            conclusion,
            context,
            comparisons,
            supplied=supplied,
            reproduction=reproduction,
            client=client,
            model=model,
            api_key=api_key,
        )
    for conclusion in rows[MAX_CONCLUSIONS:]:
        reviews[conclusion["id"]] = _open(
            "this conclusion was not reviewed because the assessment's interpretation budget was spent "
            f"on the first {MAX_CONCLUSIONS} conclusions",
            reproduction,
            next_action="review the remaining conclusions in a further assessment",
        )
    return _record(
        reviews,
        scope=scope,
        asked=asked,
        reason=None,
        reproduction=reproduction,
        deferred=max(0, len(rows) - MAX_CONCLUSIONS),
        model=model,
    )


async def _review(conclusion, context, comparisons, *, supplied, reproduction, client, model, api_key) -> dict:
    decision = await decide_with_recovery(
        intent=f"{INTENT}: {str(conclusion.get('statement'))[:120]}",
        system=_SYSTEM,
        payload=_payload(conclusion, context, comparisons),
        client=client,
        model=model,
        api_key=api_key,
        purpose=budgets.INTERPRETATION_REVIEW,
        validate=_validate(str(conclusion["id"])),
    )
    if decision.outcome != OUTCOME_OK:
        return _open(
            decision.reason or "the assessor returned no usable review of this conclusion",
            reproduction,
            next_action="ask again for this conclusion alone",
            model=model,
            failure_outcome=decision.outcome,
        )
    data = decision.data or {}
    outcome = str(data.get("outcome") or "").strip().lower()
    citations = [str(c) for c in data.get("citations") or []]
    unresolvable = [c for c in citations if c not in supplied]
    if unresolvable:
        return _open(
            f"the assessor cited {', '.join(unresolvable)}, which was not among the context supplied, so its "
            "review rests on evidence bioAF cannot check",
            reproduction,
            next_action="ask again, requiring a citation from the context supplied",
            model=model,
        )
    if outcome not in (SUPPORTED, CONTRADICTED):
        return _open(
            str(data.get("rationale") or "").strip()
            or "the assessor could not establish whether the result supports this conclusion",
            reproduction,
            model=model,
            confidence=data.get("confidence"),
        )
    return {
        "outcome": outcome,
        "rationale": str(data.get("rationale") or "").strip(),
        "inferential_step": str(data.get("inferential_step") or "").strip() or None,
        "impact": str(data.get("impact") or "").strip() or None,
        "evidence": {"citations": citations},
        "method": MODEL_ASSISTED,
        # What a reproduction established, beside the interpretation and never as it.
        "reproduction": reproduction,
        "assessor": {"model": model, "review_version": REVIEW_VERSION, "method": MODEL_ASSISTED},
        "confidence": data.get("confidence"),
        "at": _now_iso(),
    }


def _open(rationale: str, reproduction, *, next_action: str | None = None, model=None, **extra) -> dict:
    return {
        "outcome": UNRESOLVED,
        "rationale": rationale,
        "method": MODEL_ASSISTED,
        "reproduction": reproduction,
        "next_action": next_action or "supply the design, results and sample records this conclusion rests on",
        "assessor": {"model": model, "review_version": REVIEW_VERSION, "method": MODEL_ASSISTED},
        **extra,
    }


def _record(reviews, *, scope, asked, reason, reproduction, deferred: int = 0, model=None) -> dict:
    return {
        "reviews": reviews,
        "scope": scope,
        "asked": asked,
        "deferred": deferred,
        "reason": reason,
        "reproduction": reproduction,
        "review_version": REVIEW_VERSION,
        "model": model,
        "at": _now_iso(),
    }
