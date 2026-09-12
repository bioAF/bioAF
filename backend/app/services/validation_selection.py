"""change_7.5 section 2.6: select the claim and its check together, and only then the workflow.

Study 38 chose nf-core/chipseq from one paper-level method before any claim, then found both RNA-seq
contrasts incompatible with it. Here the order is reversed:

1. **Candidates** are the (claim, check) pairs a run on the requested route could execute: available,
   or unresolved only for a requirement the run itself establishes (the sample mapping, which the model
   proposes when the input is chosen and a person confirms at the gate; or what an unlisted deposit
   holds). Consistency is never a candidate; it runs on its own.
2. **Selection** (open question 2's recommendation): in autonomous mode the model chooses among the
   candidates, preferring a claim with an authors' table for comparison and an input that needs no
   unresolved mapping; in assisted mode the same ranking proposes one for a person to confirm at the
   gate. One candidate is selected without asking.
3. **Then** the workflow comes from the selected experiment's assay, the reference from that
   experiment, and the stage 1 compatibility check runs on the selection.

The record is ``analysis_selection``: ``{"current": {...}, "history": [...], "unassessed": [...]}``. A
revision is never overwritten in place; a changed selection supersedes it and is kept as history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services.llm_decision import decide
from app.services.validation_checks import (
    AUTHOR_RESULTS,
    AVAILABLE,
    CHECKS,
    candidate_pairs,
    claim_type,
)

SELECTION_INTENT = "choosing which of the paper's claims this run checks"

_CHECK_WORDS = {
    "qc_metric": "QC metric comparison",
    "author_results": "consistency with the authors' results",
    "processed_reanalysis": "reanalysis of processed data",
    "raw_reanalysis": "reanalysis from raw reads",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rank_candidates(pairs: list[dict], checks: list[dict]) -> list[dict]:
    """Candidates in the order the recommendation prefers them: a claim with an authors' table first,
    then one whose input needs no unresolved mapping, then the paper's own order."""

    def _key(pair: dict):
        claim_checks = checks[pair["claim_index"]] if pair["claim_index"] < len(checks) else {}
        has_table = ((claim_checks or {}).get(AUTHOR_RESULTS) or {}).get("status") == AVAILABLE
        settled = pair.get("status") == AVAILABLE
        return (0 if has_table else 1, 0 if settled else 1, pair["claim_index"])

    return sorted(pairs, key=_key)


def _unassessed(targets: list[dict], checks: list[dict], selected: int | None, *, route: str | None) -> list[dict]:
    """Every claim this run does not check, with the reason each check gave."""
    rows = []
    for index, claim_checks in enumerate(checks):
        if index == selected:
            continue
        reasons = [
            f"{_CHECK_WORDS[name]}: {(claim_checks.get(name) or {}).get('reason') or (claim_checks.get(name) or {}).get('status')}"
            for name in CHECKS
            if (claim_checks.get(name) or {}).get("status") != AVAILABLE
        ]
        reason = (
            "not selected for this run; " + "; ".join(reasons)
            if reasons
            else "not selected for this run; its checks are available"
        )
        rows.append({"claim_index": index, "reason": reason})
    return rows


def _prompt(targets: list[dict], pairs: list[dict], experiments: list[dict]) -> tuple[str, str]:
    system = (
        "You are choosing which ONE of a paper's claims a validation run checks. Each candidate below is a "
        "claim and the check that could test it; every candidate is one a run can execute.\n\n"
        "Respond with a SINGLE fenced JSON block (```json ... ```) and nothing else:\n"
        '{"claim_index": 0, "check": "one of the listed checks", "reason": "one sentence", "confidence": 0.0 to 1.0}\n\n'
        "Rules:\n"
        "- Choose among the candidates only.\n"
        "- Prefer a claim whose authors published a result table it can be compared with, and an input that "
        "needs no unresolved sample mapping.\n"
        "- Prefer a substantive finding over a technical quality measure."
    )
    by_id = {e.get("id"): e for e in experiments}
    lines = []
    for pair in pairs:
        target = targets[pair["claim_index"]]
        experiment = by_id.get(target.get("reported_experiment_id")) or {}
        lines.append(
            f"[claim {pair['claim_index']}] check={pair['check']} ({pair['status']}"
            f"{', needs ' + pair['requirement'] if pair.get('requirement') else ''}); "
            f"experiment {experiment.get('id')}: {experiment.get('assay')}; the paper says: {target.get('claim_text')}"
        )
    return system, "Candidates:\n" + "\n".join(lines)


async def select_analysis(
    targets: list[dict],
    checks: list[dict],
    *,
    experiments: list[dict],
    contrasts: list[dict],
    route: str | None,
    autonomous: bool,
    client,
    model: str | None,
    api_key: str | None,
    library_strategy: str | None = None,
    library_strategies: dict | None = None,
    on_issue=None,
    previous: dict | None = None,
) -> dict:
    """The selection record. ``current`` is None when nothing on the route can be checked.

    ``library_strategies`` is what each experiment's own dataset declares itself to be, by experiment
    id; ``library_strategy`` applies to an experiment it does not name."""
    from app.services.contrast_selection import INCOMPATIBLE, contrast_compatibility

    pairs = rank_candidates(candidate_pairs(targets, checks, route=route), checks)
    chosen, decided_by, reason, confidence = None, None, None, None
    if len(pairs) == 1:
        chosen, decided_by = pairs[0], "only_candidate"
        reason = "the only claim a run on this route can check"
        confidence = 1.0
    elif pairs and autonomous:
        system, payload = _prompt(targets, pairs, experiments)
        decision = await decide(
            intent=SELECTION_INTENT, system=system, payload=payload, client=client, model=model, api_key=api_key
        )
        answer = decision.data if decision.ok else {}
        picked = next(
            (
                p
                for p in pairs
                if p["claim_index"] == answer.get("claim_index") and p["check"] == answer.get("check")
            ),
            None,
        )
        if picked is not None:
            chosen, decided_by = picked, "model"
            reason, confidence = decision.reason, decision.confidence()
        else:
            if not decision.ok and on_issue:
                on_issue(decision.as_issue(impact="degraded"))
            chosen, decided_by = pairs[0], "ranking"
            reason = "the model's choice was not one of the candidates, so the ranking chose"
            confidence = None
    elif pairs:
        chosen, decided_by = pairs[0], "proposal"
        reason = "proposed for a person to confirm at the gate: it has an authors' table and the fewest open requirements"
        confidence = None

    current = None
    refusal = None
    if chosen is not None:
        target = targets[chosen["claim_index"]]
        experiment = next((e for e in experiments if e.get("id") == target.get("reported_experiment_id")), {}) or {}
        contrast_index = target.get("contrast_index") if isinstance(target.get("contrast_index"), int) else None
        workflow = experiment.get("workflow")
        if contrast_index is not None and 0 <= contrast_index < len(contrasts):
            strategy = (library_strategies or {}).get(experiment.get("id"), library_strategy)
            status, why = contrast_compatibility(contrasts[contrast_index], pipeline_key=workflow, library_strategy=strategy)
            if status == INCOMPATIBLE:
                refusal = {"outcome": "no_compatible_contrast", "reason": why}
        if refusal is None:
            reference = experiment.get("reference") or {}
            current = {
                "revision": ((previous or {}).get("current") or {}).get("revision", 0) + 1,
                "reported_experiment_id": experiment.get("id"),
                "claim_index": chosen["claim_index"],
                "check": chosen["check"],
                "claim_type": claim_type(target),
                "contrast_index": contrast_index,
                "workflow": workflow,
                "reference": {
                    "assembly": (reference.get("assembly") or {}).get("resolved"),
                    "annotation": (reference.get("annotation") or {}).get("resolved"),
                },
                "input": None,
                "sample_mapping": None,
                "predicate": None,
                "decided_by": decided_by,
                "reason": reason,
                "confidence": confidence,
                "model": model if decided_by == "model" else None,
                "superseded": False,
                "at": _now(),
            }

    history = list((previous or {}).get("history") or [])
    if previous and previous.get("current"):
        history.append({**previous["current"], "superseded": True})
    record = {
        "current": current,
        "history": history,
        "candidates": [{k: v for k, v in p.items()} for p in pairs],
        "unassessed": _unassessed(targets, checks, current["claim_index"] if current else None, route=route),
        "route": route,
    }
    if refusal is not None:
        record["refusal"] = refusal
    return record


def describe_selection(record: dict | None) -> str:
    """The selection in one sentence, for the record and the logs."""
    current = (record or {}).get("current")
    if not current:
        return json.dumps((record or {}).get("refusal") or {"current": None})
    return (
        f"claim {current['claim_index']} by {_CHECK_WORDS.get(current['check'], current['check'])} on "
        f"{current['workflow']} (experiment {current['reported_experiment_id']}, decided by {current['decided_by']})"
    )
