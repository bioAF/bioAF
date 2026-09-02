"""plan_6 step 8: in autonomous mode, the model ratifies the verdict.

``classify_study`` does not move. Deterministic code MEASURES: it still computes the suggested
verdict from the numbers, and a human can still audit exactly why a study landed where it did. That
property is the reason the classifier was written as pure rule-based code (spec-03) and losing it
would cost more than this step is worth.

What autonomous mode changes is who has the last word on the suggestion. The model receives the
comparison table, the attribution, the coverage counts and the suggested verdict, and either accepts
it or overrides it. **An override must name which evidence it is reweighing**, because an override
that reweighs nothing is an assertion rather than a judgment, and a verdict nobody can argue with is
not a verdict a scientist can use.

In assisted mode this module does nothing at all: the shipped policy stands, a clean ``validated``
auto-finalises and everything else holds at ``comparing`` for a person.
"""

from __future__ import annotations

import json
import logging
import re

from app.models.validation_study import VALIDATION_STUDY_CLASSIFICATIONS
from app.services.validation_autonomy import AUTONOMY_AUTONOMOUS

logger = logging.getLogger("bioaf.validation_ratification")

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def build_ratification_prompt(result: dict) -> tuple[str, str]:
    """Return (system, payload) asking the model to accept or override the measured verdict."""
    system = (
        "You are ratifying the verdict of an automated paper-reproduction check. Deterministic code "
        "measured the paper's claimed numbers against the numbers a pipeline computed, and proposed a "
        "verdict from those measurements. Your job is to accept that verdict or override it.\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"action": "accept" | "override", "verdict": "one of the verdicts below, when overriding", '
        '"reasoning": "one or two sentences", "evidence_reweighed": ["metric keys your override turns on"]}\n\n'
        f"Verdicts: {', '.join(VALIDATION_STUDY_CLASSIFICATIONS)}.\n\n"
        "Rules:\n"
        "- The MEASUREMENTS are not up for debate. You may not dispute a computed value or a delta; "
        "they are what makes this result auditable. You are judging what the measurements MEAN.\n"
        "- An override must name the evidence it turns on, in evidence_reweighed. An override that "
        "reweighs nothing is an assertion, and it will be discarded.\n"
        "- Accepting is the normal answer. Override when the attribution changes what the numbers "
        "mean: a divergence our own pipeline plausibly caused should not strike the paper, and an "
        "agreement on technical QC alone does not reproduce a finding.\n"
        "- Say what you actually concluded. A scientist reads this sentence next to the table."
    )
    payload = (
        f"Suggested verdict: {result.get('classification')}\n"
        f"Reasoning behind it: {result.get('reasoning')}\n\n"
        f"Coverage: {json.dumps(result.get('coverage') or {})}\n\n"
        f"Attribution (whether OUR side could explain a divergence): "
        f"{json.dumps(result.get('attribution') or {})}\n\n"
        f"Comparisons:\n{json.dumps(result.get('comparisons') or [], indent=2)}"
    )
    return system, payload


def parse_ratification(response_text: str, *, suggested: str) -> dict:
    """Read the ratification decision, falling back to the measured verdict on anything unusable.

    Every fallback path lands on ``accept`` with the suggested verdict, because the measurement is
    the thing that was actually computed. A malformed answer must never be able to move a verdict.
    """
    fallback = {
        "action": "accept",
        "verdict": suggested,
        "reasoning": "the model did not return a usable ratification, so the measured verdict stands",
        "evidence_reweighed": [],
    }
    match = _FENCED_JSON_RE.search(response_text or "")
    if not match:
        return fallback
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return fallback
    if not isinstance(data, dict):
        return fallback

    reasoning = str(data.get("reasoning") or "").strip()
    if str(data.get("action") or "").strip().lower() != "override":
        return {"action": "accept", "verdict": suggested, "reasoning": reasoning, "evidence_reweighed": []}

    verdict = str(data.get("verdict") or "").strip()
    evidence = [str(e).strip() for e in (data.get("evidence_reweighed") or []) if str(e).strip()]
    if verdict not in VALIDATION_STUDY_CLASSIFICATIONS:
        return dict(
            fallback,
            reasoning=f"the model proposed the verdict '{verdict}', which does not exist, so the measured verdict stands",
        )
    if not evidence:
        return dict(
            fallback,
            reasoning=(
                "the model overrode the verdict without naming the evidence it reweighed, so the "
                "measured verdict stands"
            ),
        )
    return {"action": "override", "verdict": verdict, "reasoning": reasoning, "evidence_reweighed": evidence}


async def ratify(result: dict, *, autonomy: str, client, model: str, api_key: str | None) -> dict | None:
    """The model's ratification of a measured verdict, or None when there is not one to apply.

    None means "change nothing": either the org is in assisted mode, or the call failed. A provider
    outage must not finalise a study on a guess, and must not discard the measurement either, so the
    study holds at ``comparing`` for a person exactly as it does in assisted mode.
    """
    if autonomy != AUTONOMY_AUTONOMOUS:
        return None

    suggested = result.get("classification")
    system, payload = build_ratification_prompt(result)
    try:
        output = await client.submit(prompt=system, payload=payload, model=model, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - an outage holds the study, it does not decide it
        logger.warning("ratification call failed, holding the study for a person: %s", exc)
        return None

    decision = parse_ratification(output, suggested=suggested)
    return {
        "action": decision["action"],
        "verdict": decision["verdict"],
        "suggested_verdict": suggested,
        "reasoning": decision["reasoning"],
        "evidence_reweighed": decision["evidence_reweighed"],
        "ratified_by": "model",
        "ratified_by_model": model,
        # Autonomous mode finalises on the model's answer without waiting for a person. The C1 gate
        # already authorised the spend; this is the science, which is the half autonomy governs.
        "finalize": True,
    }
