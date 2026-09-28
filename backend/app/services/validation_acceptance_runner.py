"""plan_8_7 stage 4: the frozen cases, run through the production assessor.

    "Use saved real artifacts through production callers, stubbing transport/provider boundaries only
    where appropriate. Do not stub the selection, scoped allocation, execution-contract propagation or
    final projection whose behavior is being accepted."

So a case is turned into the evidence a study would hold, and then the SAME `packets_for` and
`review_documents` a live assessment uses are called over it. The only double is the provider, which
is where the answers come from: a deterministic run supplies them, and a live evaluation lets the
configured model answer instead. Everything between the evidence and the verdict is production code.
"""

from __future__ import annotations

from app.services.validation_acceptance import evaluate_case, evaluate_runs


def evidence_for(case: dict) -> dict:
    """The evidence a study would hold for this frozen case, in the shape the assessment reads."""
    sources = case.get("sources") or {}
    passages = []
    for position, (kind, text) in enumerate(
        [("methods", sources.get("methods")), ("results", sources.get("results"))], start=1
    ):
        if not text:
            continue
        passages.append(
            {
                "id": f"p{position}",
                "kind": kind,
                "section": kind.title(),
                "text": str(text),
                "source": "the paper",
            }
        )
    script = sources.get("script")
    return {
        "paper_index": {"passages": passages},
        "code_inspection": {
            "sources": [{"path": "script.R", "language": "r", "text": str(script)}] if script else [],
            "manifests": [],
        },
    }


async def run_case(case: dict, *, answers: list[dict], client, model: str, api_key: str | None = None) -> dict:
    """One run of one frozen case, through the production packets and the production review."""
    from app.services.validation_documentary_review import packets_for, review_documents

    leaf = str(case.get("question") or "M4.A")
    evidence = evidence_for(case)
    packets = packets_for(evidence=evidence, plan={}, leaves=(leaf,))
    reviewed = await review_documents(packets=packets, client=client, model=model, api_key=api_key, leaves=(leaf,))
    judgment = (reviewed.get("judgments") or {}).get(leaf) or {}
    evaluation = evaluate_case(case, judgment)
    return {**evaluation, "judgment": judgment, "leaf": leaf, "model": model}


async def run_three(case: dict, *, runs: list[list[dict]], clients, model: str, api_key: str | None = None) -> dict:
    """Three independent runs of one case, evaluated together under the acceptance rules."""
    judgments = []
    for answers, client in zip(runs, clients, strict=False):
        found = await run_case(case, answers=answers, client=client, model=model, api_key=api_key)
        judgments.append(found["judgment"])
    return evaluate_runs(case, judgments, model=model)
