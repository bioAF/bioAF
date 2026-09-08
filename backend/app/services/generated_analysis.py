"""plan_7 step 18: reproduce from the described methods, when no code was published.

The notebook runs the code artifact if there is one, else the GitHub repo, **else our own
interpretation of what the paper describes.** This is that last arm, and it is the reason a paper
with no published code is still worth running.

**It is not like the other two methods, and the difference is the point:**

| Method | Determinism | Strength of a resulting verdict |
|---|---|---|
| `deseq2` / `limma_trend` | deterministic | tests the finding using OUR analysis |
| `authors_code` | deterministic given a pinned commit | strongest: tests it using THEIRS |
| `llm_from_methods` | **nondeterministic, variable run to run** | weakest, and the variability is part of the claim |

**When it runs**: rung 2 of the owner's ladder, meaning no usable published code was AVAILABLE. A
wired `deseq2` / `limma_trend` template does not pre-empt it: that row above tests the finding using
OUR analysis, which is a different claim from reproducing the paper's. It does NOT run behind a
published-code execution that was attempted and failed; that failure is the result of its own arm.

**A thin methods section does not stop it.** Step 14's sufficiency judgment is advisory and refusing
on it would contradict this plan's rule that nothing about a paper rules it in or out. An inadequate
judgment is recorded, carried into the report, and attached to whatever comes out; it does not
prevent the attempt, and a successful generated execution does not erase it.

**It runs ONCE.** An earlier draft ran it repeatedly and reported the agreement between runs, with
the repeat count left open. That is withdrawn: each run is a fresh generation, so disagreement
between runs measures the GENERATOR rather than the paper, and the compute is spent either way. The
nondeterminism is handled by what the report CLAIMS: every result from this arm is labelled as
generated from the paper's prose by a named model, carries its generated source and its assumptions
on the evidence bundle, and ranks last under step 9's qualifier.

**The factual input checks still apply.** This authorises generating from thin prose. It does not
authorise inventing sample data, nor overriding a species mismatch or a step 5/6/8 input refusal.
Judgment does not veto; facts still do.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.services.code_execution_service import (
    GENERATION_FAILED,
    METHOD_LLM_FROM_METHODS,
    QUALIFIER_ASSUMPTIONS_RECORDED,
    QUALIFIER_GENERATED_FROM_PROSE,
    QUALIFIER_METHODS_INADEQUATE,
)
from app.services.llm_decision import decide

logger = logging.getLogger("bioaf.generated_analysis")

GENERATION_INTENT = "generating an analysis from the methods the paper describes"

_LANGUAGES = ("R", "Python")

_SYSTEM = (
    "You are writing the analysis a paper describes, so it can be run against the data that paper "
    "deposited. The authors published no code, so their prose is all there is.\n\n"
    "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
    '{"language": "R" or "Python", "source": "the complete script", '
    '"entry_point": "the filename to save it as", '
    '"assumptions": ["one sentence per gap you had to fill"], '
    '"reason": "one sentence on what you wrote and why", "confidence": 0.0 to 1.0}\n\n'
    "Rules:\n"
    "- Write against the MATRIX AND DESIGN you are given. Do not invent sample names, conditions or "
    "file paths, and do not fabricate data: the script has to run on the real file at the real "
    "path, with the real column names.\n"
    "- Write the WHOLE script. It runs unattended with no one to fix an incomplete file.\n"
    "- Record every gap you filled in `assumptions`, in your own words. 'The paper does not state "
    "the FDR threshold, so 0.05 was assumed' is exactly the kind of thing a disputing reader needs, "
    "and an unstated assumption is worse than a stated one.\n"
    "- Write the results to a CSV with an id column, a log2 fold change and an adjusted p-value, so "
    "the result can be compared with the paper's own.\n"
    "- If the description does not say enough to write anything runnable, leave `source` empty and "
    "say why. That is a real answer and it is reported as a finding about the paper."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def generate_analysis(
    *,
    methods_text: str,
    matrix_description: str,
    design: dict,
    methods_inadequate: bool,
    client,
    model: str,
    api_key: str | None,
) -> dict:
    """Write the paper's analysis from its prose. **Once.** Always returns a record.

    Two terminal shapes and neither waits: an executable analysis, or ``generation_failed`` with a
    named limitation. There is no loop through fresh generations and no hold for an unspecified
    intervention.
    """
    qualifiers = [QUALIFIER_GENERATED_FROM_PROSE]
    if methods_inadequate:
        # Carried, not vetoed. Both facts are reportable and they are independent: an analysis
        # generated from thin prose can execute and agree, and that is `ran_output_agrees` CARRYING
        # `methods_inadequate`.
        qualifiers.append(QUALIFIER_METHODS_INADEQUATE)

    payload = (
        f"The data available to the script:\n{matrix_description}\n\n"
        f"The contrast to test:\n{json.dumps(design, indent=2)}\n\n"
        + (
            "NOTE: this paper's methods description has been assessed as thin. Write the best "
            "analysis the description supports and record every gap you had to fill; the result "
            "will be reported with that limitation attached.\n\n"
            if methods_inadequate
            else ""
        )
        + f"The paper's methods, verbatim:\n{(methods_text or '')[:40000]}"
    )

    decision = await decide(
        intent=GENERATION_INTENT,
        system=_SYSTEM,
        payload=payload,
        client=client,
        model=model,
        api_key=api_key,
    )
    if not decision.ok:
        return {
            "outcome": GENERATION_FAILED,
            "method": METHOD_LLM_FROM_METHODS,
            "qualifiers": qualifiers,
            "source": "",
            "assumptions": [],
            "reason": decision.reason,
            "model": model,
            "generated_at": _now(),
        }

    source = str(decision.data.get("source") or "").strip()
    if not source:
        return {
            "outcome": GENERATION_FAILED,
            "method": METHOD_LLM_FROM_METHODS,
            "qualifiers": qualifiers,
            "source": "",
            "assumptions": [],
            "reason": decision.reason or "the paper's description did not support a runnable analysis",
            "model": model,
            "generated_at": _now(),
        }

    language = str(decision.data.get("language") or "").strip()
    language = language if language in _LANGUAGES else "R"
    assumptions = [str(a).strip() for a in (decision.data.get("assumptions") or []) if str(a).strip()]
    if assumptions:
        qualifiers.append(QUALIFIER_ASSUMPTIONS_RECORDED)

    default_name = "generated_analysis.R" if language == "R" else "generated_analysis.py"
    return {
        "outcome": None,
        "method": METHOD_LLM_FROM_METHODS,
        "qualifiers": qualifiers,
        "language": language,
        "source": source,
        "entry_point": str(decision.data.get("entry_point") or "").strip() or default_name,
        # In the model's own words. This is what makes an inadequate-methods run readable rather
        # than merely suspect.
        "assumptions": assumptions,
        "reason": decision.reason,
        "confidence": decision.confidence(),
        "model": model,
        "generated_at": _now(),
    }
