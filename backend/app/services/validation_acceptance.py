"""plan_8_7 stage 4: what a material error is, and why one of them blocks acceptance.

The owner settled this on 2026-09-27:

    "Material acceptance errors block completion. Freeze source-backed expected outcomes and matched
    controls before implementation. All three independent selected-model evaluations must meet the
    case-specific material-outcome rules. A material false conclusion or missed established defect
    keeps the affected capability and full completion open until corrected and rechecked. Logging or
    averaging the error is insufficient."

A material error, from section 6 stage 4, is an unsupported conclusion, a missed established defect,
a wrong analysis or sample scope, fabricated execution or coverage, or a contradiction that would
change the user's understanding of a consequential result, method or reproduction claim. A harmless
wording difference is not one, which is why a case states what it REQUIRES and what it FORBIDS rather
than an expected sentence.

Three rules this module exists to enforce:

- **three runs, and all three must pass.** Two out of three is a model that sometimes gets it right,
  which is not an accepted capability. Averaging is explicitly not allowed.
- **the failed run is kept.** It is the evidence that the gate is open, and a diagnosis that replaces
  it with a summary is the "logging the error" the owner rejected.
- **acceptance is per model.** A second model needs its own three runs, and where none was run the
  cross-model behaviour is reported unverified rather than assumed.

The frozen cases are data, under `tests/data/plan_8_7/`. Each records who established its expectation
and from what source, because "Do not let the evaluated model's own answer establish its expected
verdict" is the failure mode this whole gate is written against.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

# The corpus ships WITH the application, not with the tests. Found by verifying the deployed
# container: the backend image carries no `tests/` directory, so a path under it resolved to nothing
# and `frozen_cases()` returned an empty tuple, which would have made the whole acceptance gate pass
# vacuously. An acceptance gate that finds no cases is the "make every difficult check grey" failure
# plan_8_7 forbids, so a missing corpus raises rather than returning nothing.
CASES = Path(__file__).resolve().parent / "acceptance_cases" / "plan_8_7"

PASSED = "passed"
MATERIAL_ERROR = "material_error"
VERDICTS = (PASSED, MATERIAL_ERROR)

# How many independent runs an acceptance needs. Not a tunable: the plan states it.
REQUIRED_RUNS = 3

# The phrases that carry each required or forbidden conclusion, keyed by the words the case uses. A
# case states its rule in English for a person to read; this is how the rule is CHECKED, and every
# entry is a structure over the answer rather than a search for a preferred sentence.
_REQUIRES: dict[str, tuple[str, ...]] = {
    "identifies that the script selects on the uncorrected p value while the methods state a "
    "Benjamini-Hochberg adjusted threshold": ("uncorrected", "unadjusted", "raw p", "pvalue", "p value"),
    "states the consequence for which results are called significant": ("significan",),
    "withdraws the criticism that the correction is not applied": ("adjusted", "padj", "corrected", "benjamini"),
    "states that whether the correction is implemented was not tested": (
        "not tested",
        "untested",
        "was not checked",
        "holds no code",
        "no code",
        "not retrieved",
        "could not",
    ),
}
_FORBIDS: dict[str, tuple[str, ...]] = {
    "asserts that the entire paper is invalid": ("entire paper", "whole paper", "the paper is invalid"),
    "asserts that the authors acted in bad faith": ("bad faith", "fabricat", "misconduct", "deliberately"),
    "reports the discrepancy as an absence of any stated correction": (
        "no correction is stated",
        "the methods state no correction",
        "correction is missing from the methods",
        "no multiple-testing correction is stated",
    ),
    "reports that the script selects on the uncorrected p value": ("uncorrected", "unadjusted", "raw p value"),
    "reports the correction as missing": ("correction is missing", "no correction", "does not correct"),
    "infers that the correction was applied": ("was applied", "is applied", "applies the correction"),
    "reports a demonstrated defect in code bioAF does not hold": (
        "the analysis code does not",
        "the script does not",
        "the code fails to",
    ),
}


@lru_cache(maxsize=None)
def frozen_cases() -> tuple[dict, ...]:
    """Every case frozen before the implementation moved, in a stable order.

    An empty corpus is an error, never an empty result: a gate with no cases would accept anything.
    """
    found = tuple(json.loads(path.read_text(encoding="utf-8")) for path in sorted(CASES.glob("*.json")))
    if not found:
        raise FileNotFoundError(
            f"no frozen acceptance cases were found under {CASES}; an acceptance gate with no cases "
            "accepts anything, so this is a broken install rather than an empty corpus"
        )
    return found


def frozen_case(name: str) -> dict:
    """One frozen case by name. A name nobody froze is a defect, not a default."""
    for case in frozen_cases():
        if case.get("case") == name:
            return case
    raise KeyError(f"{name} is not a case frozen under {CASES}")


def _text(answer: dict) -> str:
    return " ".join(
        str(answer.get(key) or "") for key in ("rationale", "impact", "next_action", "inferential_step", "statement")
    ).lower()


def _says(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(re.escape(phrase), text) for phrase in phrases)


def evaluate_case(case: dict, answer: dict) -> dict:
    """``{"verdict", "reason", "answer", "case"}``: whether ONE answer is materially right for this case.

    The outcome has to be the one the sources establish, every required conclusion has to be stated,
    and no forbidden one may be. Any of those failing is a material error: the plan's list is
    "unsupported conclusions, missed established defects, wrong analysis/sample scope, fabricated
    execution/coverage, or contradictions that would change the user's understanding".
    """
    answer = answer if isinstance(answer, dict) else {}
    text = _text(answer)
    outcome = str(answer.get("outcome") or "").strip().lower()
    expected = str(case.get("expected_outcome") or "").strip().lower()
    if outcome != expected:
        return _verdict(
            case,
            answer,
            MATERIAL_ERROR,
            f"the sources establish {expected} for this case and the answer was {outcome or 'nothing'}",
        )
    # What it MUST NOT say is checked first: an answer that reached the right outcome and asserted the
    # whole paper is invalid is a worse error than one that under-stated its finding, and reporting the
    # missing requirement instead would send whoever reads it to the wrong repair.
    for forbidden in case.get("forbidden") or []:
        phrases = _FORBIDS.get(forbidden)
        if phrases is None:
            continue
        if _says(text, phrases):
            return _verdict(case, answer, MATERIAL_ERROR, f"the answer states what this case forbids: {forbidden}")
    for requirement in case.get("required") or []:
        phrases = _REQUIRES.get(requirement)
        if phrases is None:
            continue
        if not _says(text, phrases):
            return _verdict(
                case, answer, MATERIAL_ERROR, f"the answer does not state what this case requires: {requirement}"
            )
    return _verdict(case, answer, PASSED, None)


def _verdict(case: dict, answer: dict, verdict: str, reason: str | None) -> dict:
    return {"case": case.get("case"), "verdict": verdict, "reason": reason, "answer": answer}


def evaluate_runs(case: dict, answers: list[dict], *, model: str | None = None) -> dict:
    """Three independent runs against one frozen case. Every one of them has to pass.

    ``cross_model`` is always ``unverified`` here: this evaluates ONE model, and the plan is explicit
    that acceptance claimed for a model needs that model's own runs. `accepted_models` is what reads
    several of these together.
    """
    rows = [answer for answer in answers or [] if isinstance(answer, dict)]
    evaluations = [evaluate_case(case, answer) for answer in rows]
    errors = [row for row in evaluations if row["verdict"] == MATERIAL_ERROR]
    enough = len(rows) >= REQUIRED_RUNS
    return {
        "case": case.get("case"),
        "model": model,
        "runs": len(rows),
        "evaluations": evaluations,
        "material_errors": len(errors),
        "accepted": enough and not errors,
        "reason": None
        if enough and not errors
        else (
            f"an acceptance needs three independent runs and this has {len(rows)}"
            if not enough
            else f"{len(errors)} of {len(rows)} runs made a material error, and one keeps this capability open: "
            + "; ".join(str(row["reason"]) for row in errors)
        ),
        # Never assumed from another model's result. "Do not claim model portability from mocked
        # responses", and a model with no runs of its own has none.
        "cross_model": "unverified",
    }


def accepted_models(runs: dict[str, dict]) -> list[str]:
    """The models whose OWN three runs were clean. A model that was not evaluated is not accepted."""
    return sorted(name for name, row in (runs or {}).items() if isinstance(row, dict) and row.get("accepted"))
