"""plan_8_7 stage 2: the two or three sentences that lead the report, and what they may not do.

    "The synthesis may be model-assisted within the assessment budget, but it may only summarize
    accepted scoped outcomes. It cannot award points, create new findings or upgrade reproduction
    status. Its references are validated against the published revision, and a failed synthesis falls
    back to a factual summary of those outcomes."

So this is deliberately the thinnest model call bioAF makes. It is handed the accepted outcomes and
asked for prose. Everything it returns is filtered against them:

- **a reference to an obligation nobody settled is dropped.** The summary's own rows are the universe.
- **a score, a finding or a verdict it invents is not read at all.** Only `lead`,
  `most_consequential` and `untested` are taken out of the answer, and the reproduction statement is
  copied from the record rather than from the model.
- **a failure is not an error.** No model, an unreachable provider or an unusable answer returns the
  factual summary, which is the same content in bioAF's own words.
"""

from __future__ import annotations

import logging

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, decide_with_recovery

logger = logging.getLogger("bioaf.validation_report_synthesis")

FACTUAL = "factual"
MODEL_ASSISTED = "model_assisted"

INTENT = "writing the two or three sentences that lead a paper's assessment report"

MAX_LEAD_CHARS = 700

_SYSTEM = (
    "You are writing the SHORT lead of a report about one scientific paper, for a scientist deciding "
    "whether to rely on it. Below are the conclusions bioAF has already established. Summarise them.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"lead": "two or three sentences", "most_consequential": ["the ids of the concerns that matter '
    'most"], "untested": ["the ids of the questions a reader most needs settled"]}\n\n'
    "Rules:\n"
    "- summarise ONLY what is listed below. Do not add a finding, a concern or an observation of your own.\n"
    "- do not produce a score, a grade, a percentage or a verdict about the paper.\n"
    "- do not say anything about whether the results were reproduced. That is recorded separately and "
    "your answer cannot change it.\n"
    "- name the ids you are pointing at exactly as they are written below.\n"
    "- plain language. The reader is a scientist, not a bioAF engineer, so do not mention obligations, "
    "rubrics, leaves, packets or scores.\n"
    "- what is untested is untested: say bioAF could not settle it, never that the paper omitted it."
)


def _factual_lead(summary: dict) -> str:
    """bioAF's own words for the same content. This is the fallback and the floor."""
    if summary.get("reason"):
        return str(summary["reason"])
    parts: list[str] = []
    supported, concerns, untested = (
        summary.get("supported_count") or 0,
        summary.get("concern_count") or 0,
        (summary.get("untested_count") or 0),
    )
    if concerns:
        first = (summary.get("concerns") or [{}])[0]
        parts.append(
            f"{concerns} demonstrated concern{'' if concerns == 1 else 's'}, the most consequential being: "
            + str(first.get("statement") or "").rstrip(".")
            + "."
        )
    if supported:
        first = (summary.get("supported") or [{}])[0]
        parts.append(
            f"{supported} supported observation{'' if supported == 1 else 's'}, including: "
            + str(first.get("statement") or "").rstrip(".")
            + "."
        )
    if untested:
        parts.append(f"{untested} question{'' if untested == 1 else 's'} bioAF could not settle.")
    reproduction = summary.get("reproduction") or {}
    if not reproduction.get("attempted"):
        parts.append("No independent reproduction of this paper's results has been attempted.")
    return " ".join(parts) or "bioAF established nothing about this paper."


def _fallback(summary: dict, *, reason: str | None = None) -> dict:
    return {
        "lead": _factual_lead(summary),
        "most_consequential": [str(row.get("leaf")) for row in summary.get("concerns") or []],
        "untested": [str(row.get("leaf")) for row in summary.get("untested") or []],
        "method": FACTUAL,
        "assessment_revision": summary.get("assessment_revision"),
        "reproduction": summary.get("reproduction") or {},
        "reason": reason,
    }


def _payload(summary: dict) -> str:
    def rows(key: str, label: str) -> str:
        found = summary.get(key) or []
        if not found:
            return f"{label}: none.\n"
        lines = "\n".join(
            f"  [{row.get('leaf')}] {row.get('statement')}"
            + (f" Consequence: {row['impact']}" if row.get("impact") else "")
            for row in found
        )
        return f"{label}:\n{lines}\n"

    return (
        rows("supported", "Supported observations")
        + rows("concerns", "Demonstrated concerns")
        + rows("untested", "Questions bioAF could not settle")
    )


def _validate(data: dict) -> list[str]:
    problems: list[str] = []
    if not str((data or {}).get("lead") or "").strip():
        problems.append("`lead` must be two or three sentences summarising the conclusions listed")
    return problems


async def synthesize(*, summary: dict, client, model: str, api_key: str | None) -> dict:
    """``{"lead", "most_consequential", "untested", "method", ...}``. Never raises.

    ``summary`` is `validation_report_areas.assessment_summary`. A model answer is filtered against it;
    anything else falls back to that summary in bioAF's own words.
    """
    summary = summary or {}
    known = {str(row.get("leaf")) for key in ("supported", "concerns", "untested") for row in summary.get(key) or []}
    if client is None or not model or not known:
        return _fallback(summary, reason=None if known else "there are no established conclusions to summarise")
    try:
        decision = await decide_with_recovery(
            intent=INTENT,
            system=_SYSTEM,
            payload=_payload(summary),
            client=client,
            model=model,
            api_key=api_key,
            purpose=budgets.REPORT_SYNTHESIS,
            validate=_validate,
        )
    except Exception as exc:  # noqa: BLE001 - a summary cannot fail the report it leads
        logger.warning("the report synthesis could not run: %s", exc)
        return _fallback(summary, reason=str(exc))
    if decision.outcome != OUTCOME_OK:
        return _fallback(summary, reason=decision.reason or decision.outcome)
    data = decision.data or {}
    # Only these three fields are read. A score, a verdict or a finding the answer invented is not in
    # the result at all, rather than being filtered out of a shape that could carry it.
    return {
        "lead": str(data.get("lead") or "").strip()[:MAX_LEAD_CHARS],
        "most_consequential": [str(row) for row in data.get("most_consequential") or [] if str(row) in known],
        "untested": [str(row) for row in data.get("untested") or [] if str(row) in known],
        "method": MODEL_ASSISTED,
        "model": model,
        "assessment_revision": summary.get("assessment_revision"),
        # Copied from the record, never from the answer: a synthesis cannot upgrade what a run did.
        "reproduction": summary.get("reproduction") or {},
        "reason": None,
    }
