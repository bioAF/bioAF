"""plan_7 step 17: was this noise read as signal, and what else could explain the difference?

Two model calls, both made AFTER step 17 or step 18 has executed code and the result diverges from
the paper. Neither fires on the pipeline route: that is a deliberate scope line, because only the
arms that ran code give us a like-for-like result to set against the paper's own. A pipeline-route
divergence like study 26's 7,389 vs 4,054 peaks still gets prose only.

**The tool never concludes that the authors got it wrong.** An earlier draft had
``authors_misinterpreted`` as an outcome the classifier could pick, described as the one that
indicts the science rather than the artefact. That was removed. Divergence is always "we could not
reproduce", and the possibility that a paper read noise as signal is carried BESIDE the outcome as a
flagged possible issue, hedged, with our numbers shown next to theirs so the reader judges.

This is also why no human ratification step is needed here: a hedged possible-issue flag beside both
sets of numbers is not an accusation, and requiring a person would put a mandatory human step inside
a feature whose acceptance criterion is a fully autonomous run.

**The causal assessment's candidate set always includes bioAF's own side.** Their code, run by us,
on data we selected and mounted, with arguments a model chose, in an environment we built: any of
those can produce a different number, and ours is usually the cheapest to check. Study 26's own
reasoning is the standard: "a peak-caller/threshold difference on our side plausibly explains the
gap, so the paper cannot be indicted."
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.code_execution_service import METHOD_AUTHORS_CODE, METHOD_LLM_FROM_METHODS
from app.services.llm_decision import decide

logger = logging.getLogger("bioaf.signal_assessment")

LIKELY = "likely"
NOT_LIKELY = "not likely"

SIGNAL_INTENT = "judging whether the paper's result could be noise read as signal"
CAUSE_INTENT = "weighing what could explain the difference between the paper's result and ours"

# Only the arms that ran code. Everything else has no like-for-like comparison to reason from.
_EXECUTION_METHODS = (METHOD_AUTHORS_CODE, METHOD_LLM_FROM_METHODS)

# Outcomes a transcript ESTABLISHES. Asking a model to speculate about one of these would put a
# guess beside a fact: the lockfile named a package that no longer resolves, and that is the cause.
_ESTABLISHED = ("dependency_unresolvable", "code_incomplete", "code_error", "ran_no_output", "code_unreachable")

# The candidates, ours first and named explicitly, so a model reaching for the paper has to pass
# bioAF's own side on the way.
CAUSE_CANDIDATES = (
    "bioaf_input_mapping",
    "bioaf_arguments",
    "bioaf_environment",
    "deposit_problem",
    "code_defect",
    "unresolved",
)

# In the model's language, not ours: the prompt asks about the paper, and "authors_code" is a token
# from a state machine it has never seen.
_METHOD_LABEL = {
    METHOD_AUTHORS_CODE: "the authors' own published code",
    METHOD_LLM_FROM_METHODS: "an analysis generated from the paper's prose",
}

_SIGNAL_SYSTEM = (
    "You are comparing a published result with the result an independent re-run produced, and "
    "answering ONE narrow question: could the paper's number plausibly be noise read as signal?\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"verdict": "likely" or "not likely", "reason": "one or two sentences", '
    '"confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- This is NOT an accusation and it will never be published as a verdict. A 'likely' is rendered "
    "as a hedged possible issue beside both sets of numbers, and the reader judges.\n"
    "- 'not likely' is the ordinary answer. Say it whenever the paper's number looks like a real "
    "measurement, whatever the size of the difference.\n"
    "- Weak enrichment, a low signal-to-noise ratio, a small number of events, or a result at the "
    "edge of a threshold are what make 'likely' reasonable. A large difference on its own is not.\n"
    "- Hedge. You are reasoning from two numbers and a little context, not from the raw data."
)

_CAUSE_SYSTEM = (
    "An independent re-run of a paper's published analysis produced a different result. You are "
    "naming the MOST LIKELY explanation from a fixed list, or declining to name one.\n\n"
    f"Candidates: {', '.join(CAUSE_CANDIDATES)}.\n\n"
    "  bioaf_input_mapping - we chose which deposited file to mount and how its columns map to "
    "conditions. Both were model decisions and either can be wrong.\n"
    "  bioaf_arguments - we chose the entry point and its arguments from a file listing.\n"
    "  bioaf_environment - we built the container and installed the dependencies.\n"
    "  deposit_problem - the deposited data itself does not match what the analysis expects.\n"
    "  code_defect - the published code does not do what the paper says it does.\n"
    "  unresolved - the evidence does not reach any of these.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"candidate": "one of the listed values", "reason": "one or two sentences", '
    '"confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- 'unresolved' is a real answer and often the right one. 'We ran their code and got a "
    "different number, and we do not know why' is a publishable, honest result.\n"
    "- Name 'code_defect' only where specific evidence demonstrates it. A difference in a number is "
    "not that evidence.\n"
    "- OUR side is a candidate in every divergence and is usually the cheapest to check."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def assess_signal(
    *,
    method: str,
    paper_value,
    our_value,
    metric: str | None,
    context: str,
    client,
    model: str,
    api_key: str | None,
) -> dict | None:
    """Could the paper's result be a misinterpretation of noise as signal? Or None.

    None means the question was not asked or could not be answered, which is the honest state: an
    invented assessment would be an accusation nobody made.
    """
    if method not in _EXECUTION_METHODS:
        return None
    if paper_value is None or our_value is None:
        return None

    payload = (
        f"Metric: {metric or 'the reported result'}\n"
        f"The paper reports: {paper_value}\n"
        f"Our re-run of the authors' own analysis produced: {our_value}\n"
        f"{('Context: ' + context) if (context or '').strip() else ''}"
    )
    decision = await decide(
        intent=SIGNAL_INTENT,
        system=_SIGNAL_SYSTEM,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        allowed=[LIKELY, NOT_LIKELY],
    )
    if not decision.ok:
        return None
    verdict = decision.choice("verdict")
    if verdict is None:
        logger.info("signal assessment returned no usable verdict while %s", SIGNAL_INTENT)
        return None
    return {
        "verdict": verdict,
        "reason": decision.reason,
        "confidence": decision.confidence(),
        "model": model,
        "assessed_at": _now(),
    }


async def assess_causes(*, observation: dict, method: str, client, model: str, api_key: str | None) -> dict | None:
    """What could explain the observation, or None when there is nothing to weigh.

    Stored as its OWN key so it is never mistaken for the observation. A cause is stated as
    established only where specific evidence demonstrates it; where the evidence does not reach that
    far, the candidate is ``unresolved`` and the report says so rather than picking one.
    """
    outcome = str((observation or {}).get("outcome") or "")
    if method not in _EXECUTION_METHODS:
        return None
    if outcome in _ESTABLISHED:
        # Established by the transcript. A model's opinion here would sit beside a fact and read
        # like a competing one.
        return None

    payload = (
        f"What the run did: {outcome}\n"
        f"The paper reports: {observation.get('paper_value')}\n"
        f"Our run produced: {observation.get('our_value')}\n"
        f"Metric: {observation.get('metric') or 'not stated'}\n"
        f"Method: {_METHOD_LABEL[method]}\n"
        f"Last of the transcript:\n{(observation.get('transcript_tail') or '')[-2000:]}"
    )
    decision = await decide(
        intent=CAUSE_INTENT,
        system=_CAUSE_SYSTEM,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
        allowed=list(CAUSE_CANDIDATES),
    )
    if not decision.ok:
        return None

    candidate = decision.choice("candidate")
    reason = decision.reason
    if candidate is None:
        reason = reason or "the model named an explanation outside the candidate set, so the cause is unresolved"
    return {
        "candidate": None if candidate == "unresolved" else candidate,
        "candidates_offered": list(CAUSE_CANDIDATES),
        "reason": reason,
        "confidence": decision.confidence(),
        "model": model,
        "assessed_at": _now(),
    }
