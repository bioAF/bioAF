"""plan_8_5 section 3.6: the production caller for the rubric's documentary judgments.

`validation_judgment` declared what bioAF may ask a model about one obligation and what it does with
the answer, and nothing ever called it. So every experimental obligation and most of the
computational ones stayed declared capability limits: an implemented response schema is not an
implemented assessor.

This is the caller. It holds to the rules the contract was written for:

- **one obligation per request**, with the paper's own passages in front of the assessor and its own
  words as the question. No holistic rating, no number, no rubric in the paper-reading response;
- **a failure affects its own obligation only.** A provider that is unreachable for one question
  leaves that one leaf grey with its next action, and the other answers stand;
- **nothing is asked without evidence.** No passages, no provider, no judgment, and the reason says
  which it was;
- **a bounded budget**, declared in the decision audit like every other model call bioAF makes.

It asks. What an answer establishes is `validation_judgment`'s to decide, and what it is worth is
the rubric's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services import validation_decision_budgets as budgets
from app.services.llm_decision import OUTCOME_OK, decide_with_recovery
from app.services.validation_judgment import CONTRACT_VERSION, JudgmentRefused, build_request, judgment_from

logger = logging.getLogger("bioaf.validation_documentary_review")

# The obligations a bounded model review can establish from the paper's own text. Everything else in
# the rubric is measured, or needs a run. C5 is here because "does this operation suit this input"
# is a judgment about documented behaviour; C1 to C4 are read by a parser and never asked.
JUDGED_LEAVES = (
    "S2.A",
    "S2.B",
    "S5.A",
    "E1.A",
    "E1.B",
    "E2.A",
    "E2.B",
    "E3.A",
    "E3.B",
    "M1.A",
    "M1.B",
    "M3.A",
    "M3.B",
    "M5.A",
    "M5.B",
    "C5.A",
    "C5.B",
)

REVIEW_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# How much of one piece of evidence reaches the assessor. A passage is a sentence or a record, not
# a document: the contract is that every citation resolves to one thing a reader can check.
MAX_PASSAGE_CHARS = 900
MAX_RECORDS = 24


def passages_for(*, evidence: dict | None, plan: dict | None) -> list[dict]:
    """The evidence a documentary judgment may rest on, each piece with an id and its source.

    plan_8_5 section 3.6: the paper's own methods sentences and what it says about its population,
    the passages its claims sit in, the deposit's own sample records, and the tools the analysis
    declares. Held evidence only: nothing is fetched and no paper is read again.
    """
    evidence = evidence or {}
    plan = plan or {}
    rows: list[dict] = []
    passages = evidence.get("paper_passages") or {}
    for index, sentence in enumerate(passages.get("methods") or [], start=1):
        rows.append({"id": f"m{index}", "source": "the paper's methods", "text": str(sentence)[:MAX_PASSAGE_CHARS]})
    for index, sentence in enumerate(passages.get("statements") or [], start=1):
        rows.append(
            {
                "id": f"s{index}",
                "source": "the paper on its samples",
                "text": str(sentence)[:MAX_PASSAGE_CHARS],
            }
        )
    for index, claim in enumerate(passages.get("claims") or [], start=1):
        text = str((claim or {}).get("passage") or "").strip()
        if text:
            rows.append({"id": f"c{index}", "source": "the paper's results", "text": text[:MAX_PASSAGE_CHARS]})
    held = evidence.get("sample_records") if isinstance(evidence.get("sample_records"), dict) else {}
    for deposit in (held or {}).get("deposits") or []:
        for sample in (deposit.get("samples") or [])[:MAX_RECORDS]:
            described = "; ".join(
                part
                for part in [
                    str(sample.get("title") or ""),
                    str(sample.get("organism") or ""),
                    str(sample.get("source_name") or ""),
                    *[f"{k}: {v}" for k, v in (sample.get("characteristics") or {}).items()],
                    *[str(u) for u in sample.get("unkeyed") or []],
                ]
                if part
            )
            if described:
                rows.append(
                    {
                        "id": f"{deposit.get('accession')}/{sample.get('accession')}",
                        "source": f"the deposited record of {deposit.get('accession')}",
                        "text": described[:MAX_PASSAGE_CHARS],
                    }
                )
    tools = [str(t).strip() for t in (plan.get("method") or {}).get("tools") or [] if str(t).strip()]
    packages: list[str] = []
    for source in ((evidence.get("code_inspection") or {}).get("sources") or [])[:MAX_RECORDS]:
        if not isinstance(source, dict):
            continue
        if str(source.get("language") or "").lower() == "r":
            from app.services.r_parser import read_r

            found = read_r(str(source.get("text") or ""))
            packages += found["packages"] + found["namespaced"]
    declared = sorted(set(tools) | set(packages))
    if declared:
        rows.append(
            {
                "id": "t1",
                "source": "the tools this analysis declares",
                "text": "The analysis declares: " + ", ".join(declared),
            }
        )
    return rows


def _validate(data: dict) -> list[str]:
    """The caller's own schema, which is what earns one semantic re-ask instead of a wasted call."""
    problems = []
    outcome = str((data or {}).get("outcome") or "").strip().lower()
    if outcome not in ("met", "unmet", "cannot_establish"):
        problems.append("`outcome` must be exactly one of met, unmet, cannot_establish")
    if outcome in ("met", "unmet") and not (data or {}).get("citations"):
        problems.append("an answer of met or unmet must cite the ids of the passages it rests on")
    return problems


async def review_documents(
    *,
    passages: list[dict] | None,
    client,
    model: str,
    api_key: str | None,
    leaves: tuple[str, ...] = JUDGED_LEAVES,
    on_failure=None,
) -> dict:
    """``{"judgments", "failures", "at", "model", "reason"}``: one judgment per obligation asked.

    ``passages`` are the paper's own text with ids, which is the only evidence an answer may rest on.
    """
    accepted: dict[str, dict] = {}
    failures: list[dict] = []
    rows = [p for p in passages or [] if isinstance(p, dict) and p.get("id") and str(p.get("text") or "").strip()]
    if not rows:
        return _nothing("bioAF holds no passages of this paper to judge these obligations on")
    if client is None or not model:
        return _nothing("no language model is configured for this organisation, so nothing was judged")
    for leaf in leaves:
        try:
            request = build_request(leaf, passages=rows)
        except JudgmentRefused as refusal:
            failures.append({"leaf": leaf, "reason": str(refusal)})
            continue
        decision = await decide_with_recovery(
            intent=f"judging {request['criterion']} {leaf.rpartition('.')[2]}: {request['title']}",
            system=request["system"],
            payload=request["payload"],
            client=client,
            model=model,
            api_key=api_key,
            purpose=budgets.DOCUMENTARY_JUDGMENT,
            validate=_validate,
        )
        if decision.outcome != OUTCOME_OK:
            failures.append({"leaf": leaf, "reason": decision.reason or decision.outcome, "outcome": decision.outcome})
            if on_failure is not None:
                on_failure(decision)
        accepted[leaf] = judgment_from(leaf, decision.data if decision.outcome == OUTCOME_OK else None, passages=rows, model=model)
        accepted[leaf]["evidence_ids"] = [p["id"] for p in rows]
    return {
        "judgments": accepted,
        "failures": failures,
        "at": _now_iso(),
        "model": model,
        "contract_version": CONTRACT_VERSION,
        "review_version": REVIEW_VERSION,
        "reason": None,
    }


def _nothing(reason: str) -> dict:
    return {
        "judgments": {},
        "failures": [],
        "at": _now_iso(),
        "model": None,
        "contract_version": CONTRACT_VERSION,
        "review_version": REVIEW_VERSION,
        "reason": reason,
    }
