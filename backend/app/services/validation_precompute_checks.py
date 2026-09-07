"""plan_7 step 14: the cheap checks, before any Kubernetes compute.

DESIRED STATE phase 2. Four questions whose answers decide whether spending compute is worthwhile,
and what to spend it on:

- Does the sample data look correct, judged on filenames, file types and what the paper describes?
- Do the sample files align with the recorded metadata: correct species, names, counts?
- Does the paper contain a detailed enough methods section to glean anything from?
- Does the paper say enough about its samples to glean anything from?

**These run at READ time, on either route.** Everything they need exists by then: step 13 fetched the
deposit inventory and the sample manifest, and the extraction produced ``sample_sheet.organism``, so
they cost no additional HTTP call. It is also the only timing that works, because step 15 renders
them at the C1 gate and the gate is pre-approval: run them in the driver and the approver authorises
the spend without the information these checks exist to give them.

**The cost, stated plainly.** Two of the four are model judgments, so this spends two LLM calls on
every paper read, including papers nobody approves. Both are cheap calls on text already in hand.

**Deterministic blocks, judgment advises.** A species mismatch is a string comparison, not an
opinion, and it invalidates every number downstream: a human-genome run on mouse data is a confident
wrong answer that costs hours first. The other three are model judgments about sufficiency, and
refusing on one would contradict this plan's own rule that nothing about a paper rules it in or out.
A thin methods section is a FINDING, and step 18 exists to attempt it anyway.

Same split steps 5, 6 and 8 already use: refuse on a fact, report on a judgment.
"""

from __future__ import annotations

import logging

from app.services.llm_decision import decide

logger = logging.getLogger("bioaf.validation_precompute")

OK = "ok"
MISMATCH = "mismatch"
UNKNOWN = "unknown"

CHECK_SAMPLE_DATA = "sample_data_matches_paper"
CHECK_SPECIES = "species_matches"
CHECK_METHODS = "methods_detailed_enough"
CHECK_SAMPLES_DESCRIBED = "samples_described_enough"

METHODS_INTENT = "judging whether the paper's methods are detailed enough to reproduce"
SAMPLES_INTENT = "judging whether the paper describes its samples well enough to use"


def _result(
    check: str,
    verdict: str,
    *,
    detail: str,
    blocking: bool = False,
    decided_by: str = "measurement",
    model: str | None = None,
    reason: str = "",
    confidence: float = 0.0,
) -> dict:
    return {
        "check": check,
        "verdict": verdict,
        "detail": detail,
        # Only a FACT blocks. Recorded per-check rather than derived at the gate, so the gate reads
        # one field instead of re-encoding which checks are opinions.
        "blocking": blocking,
        "decided_by": decided_by,
        "model": model,
        "reason": reason,
        "confidence": confidence,
    }


def _norm(organism: str | None) -> str:
    return " ".join((organism or "").strip().lower().split())


def check_species(plan_organism: str | None, deposit_organisms: list[str] | None) -> dict:
    """The plan's organism against the deposit's own declaration. **This one blocks approval.**

    Nothing in the product checks species today. A human-genome run on mouse data produces numbers
    that look fine and are wrong, and it costs hours before anyone can see that.

    A deposit declaring MORE than one organism is not automatically a mismatch: a xenograft or a
    spike-in deposits two, and the plan names the one being analysed. The mismatch is the plan's
    organism being absent from the deposit's set.
    """
    wanted = _norm(plan_organism)
    declared = [d for d in ((_norm(o) for o in deposit_organisms or [])) if d]
    if not wanted:
        return _result(CHECK_SPECIES, UNKNOWN, detail="the reproduction plan does not state the paper's organism")
    if not declared:
        # Not knowing is not the same as disagreeing, and blocking here would refuse every deposit
        # whose series matrix omits the field.
        return _result(CHECK_SPECIES, UNKNOWN, detail="the deposit declares no organism to compare against")
    if wanted in declared:
        return _result(CHECK_SPECIES, OK, detail=f"the paper and the deposit both name {plan_organism}")
    return _result(
        CHECK_SPECIES,
        MISMATCH,
        detail=(
            f"The paper's plan names {plan_organism} and the deposit declares "
            f"{', '.join(sorted(set(deposit_organisms or [])))}. Running one against the other would "
            "align to the wrong genome and produce numbers that look valid and are not."
        ),
        blocking=True,
    )


def check_sample_data(*, paper_sample_count: int | None, entries: list) -> dict:
    """The deposit's per-sample files against the number of samples the paper describes.

    Advisory. A deposit holding ONE series-level matrix with every sample as a column is the
    commonest usable shape, and counting it as one sample would flag every well-formed deposit, so
    a series-level matrix satisfies the check on its own.
    """
    if not paper_sample_count:
        return _result(CHECK_SAMPLE_DATA, UNKNOWN, detail="the paper does not state how many samples it used")
    if not entries:
        # Nothing listed is a discovery problem, and step 13's own row already says so. Calling it a
        # mismatch here would report the same failure twice, as two different kinds of thing.
        return _result(CHECK_SAMPLE_DATA, UNKNOWN, detail="no deposited files were listed to compare against")

    reproducible = {"matrix_counts", "matrix_normalized", "peaks", "barcodes", "features"}
    series_level = [e for e in entries if getattr(e, "level", None) == "series" and e.classification in reproducible]
    if series_level:
        return _result(
            CHECK_SAMPLE_DATA,
            OK,
            detail=(
                f"the deposit holds {len(series_level)} series-level file(s) that carry every sample as "
                "a column, which matches a study of any size"
            ),
        )

    per_sample = {e.gsm for e in entries if getattr(e, "level", None) == "sample" and e.classification in reproducible}
    if len(per_sample) == paper_sample_count:
        return _result(
            CHECK_SAMPLE_DATA,
            OK,
            detail=f"the deposit holds usable files for {len(per_sample)} sample(s), matching the paper",
        )
    kinds = sorted({e.classification for e in entries})
    return _result(
        CHECK_SAMPLE_DATA,
        MISMATCH,
        detail=(
            f"The paper describes {paper_sample_count} sample(s) and the deposit holds usable files for "
            f"{len(per_sample)}. What is deposited: {', '.join(kinds)}."
        ),
    )


_JUDGMENT_SYSTEM = (
    "You are judging whether a paper says enough for someone else to redo its analysis. Answer the "
    "ONE question below about the text you are given, and nothing else.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"answer": "yes" or "no", "reason": "one sentence", "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- Judge the TEXT in front of you. Do not assume a detail is present because papers usually "
    "state it, and do not penalise a paper for something the question does not ask about.\n"
    "- 'no' is a real answer and a useful one. It does not stop anything; it is recorded beside "
    "whatever the reproduction produces, so a reader can weigh the result against the description "
    "it came from.\n"
    "- Say what is missing, not that something is missing."
)

_METHODS_QUESTION = (
    "Question: is the computational methods description detailed enough to write an equivalent "
    "analysis from? Enough means the tools, the reference and the thresholds are recoverable, not "
    "that every parameter is listed."
)

_SAMPLES_QUESTION = (
    "Question: does the paper say enough about its samples to tell which sample belongs to which "
    "condition? Enough means the per-sample condition assignment is recoverable from the text, not "
    "that every sample is named."
)


async def _judge(
    *, check: str, intent: str, question: str, text: str, client, model: str, api_key: str | None, on_issue
) -> dict:
    """One sufficiency judgment, through the plan_6 seam: a stated choice, a reason, a confidence.

    Never blocking. A paper we could not read is not a paper with a thin methods section, so an
    empty text asks nothing and answers UNKNOWN rather than spending a call to say so.
    """
    if not (text or "").strip():
        return _result(check, UNKNOWN, detail="there was no text to judge")

    decision = await decide(
        intent=intent,
        system=f"{_JUDGMENT_SYSTEM}\n\n{question}",
        payload=text,
        client=client,
        model=model,
        api_key=api_key,
        allowed=["yes", "no"],
    )
    if not decision.ok:
        if on_issue:
            on_issue(decision.as_issue(impact="degraded"))
        return _result(check, UNKNOWN, detail=decision.reason)

    answer = decision.choice("answer")
    if answer is None:
        return _result(check, UNKNOWN, detail="the model did not answer yes or no", decided_by="model", model=model)
    return _result(
        check,
        OK if answer == "yes" else MISMATCH,
        detail=decision.reason,
        decided_by="model",
        model=model,
        reason=decision.reason,
        confidence=decision.confidence(),
    )


async def run_precompute_checks(
    *,
    methods_text: str,
    samples_text: str,
    plan_organism: str | None,
    deposit_organisms: list[str] | None,
    paper_sample_count: int | None,
    entries: list,
    client,
    model: str,
    api_key: str | None,
    on_issue=None,
) -> dict:
    """All four checks, keyed by check name, for ``evidence["precompute_checks"]``.

    The two facts are answered first and independently of the model, so a provider outage cannot
    take the species hold with it.
    """
    checks = {
        CHECK_SPECIES: check_species(plan_organism, deposit_organisms),
        CHECK_SAMPLE_DATA: check_sample_data(paper_sample_count=paper_sample_count, entries=entries),
    }
    checks[CHECK_METHODS] = await _judge(
        check=CHECK_METHODS,
        intent=METHODS_INTENT,
        question=_METHODS_QUESTION,
        text=methods_text,
        client=client,
        model=model,
        api_key=api_key,
        on_issue=on_issue,
    )
    checks[CHECK_SAMPLES_DESCRIBED] = await _judge(
        check=CHECK_SAMPLES_DESCRIBED,
        intent=SAMPLES_INTENT,
        question=_SAMPLES_QUESTION,
        text=samples_text,
        client=client,
        model=model,
        api_key=api_key,
        on_issue=on_issue,
    )
    return checks


def species_hold(precompute_checks: dict | None) -> str | None:
    """The sentence that refuses approval, or None.

    **Not a driver hold.** ``_hold_deposit`` writes ``evidence["deposit_failed"]``, is specific to
    the deposit route, and its escape hatch is the ``acquiring_processed -> acquiring_data`` edge.
    These checks run BEFORE approval, so there is no driver state to hold in; the gate already
    blocks on other conditions and this is one more.
    """
    check = (precompute_checks or {}).get(CHECK_SPECIES) or {}
    if check.get("verdict") != MISMATCH:
        return None
    return check.get("detail") or "the paper's organism and the deposit's declared organism disagree"
