"""plan_8_1 section 3.2 and decision D4: the workflow checks one approval covers.

A study ran one analysis after approval and wrote its result into single slots. Now an approval covers a
specified set of workflow checks, recorded as check records carrying the ``approval_id`` and the
``analysis_key`` their execution is fingerprinted by:

- the selected claim's check on the approved route (processed reanalysis on the deposit route, raw
  reanalysis on the pipeline route);
- every other claim that the same analysis supports (the same contrast, the same experiment, the same
  check available): they share the execution, and each keeps its own record.

No workflow check runs outside an approved set: a record without an approval is never launched. When the
study concludes, each record is given its outcome from the evidence the run produced, and the one analysis
is referenced by every record that shares it. Credit stays per finding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services import validation_check_queue as queue

# plan_8_1 label, pending the owner's sign-off.
SHARED_PREDICATE_REASON = (
    "the shared analysis was compared at the selected claim's definition only, so this claim's own "
    "definition was not compared"
)


def _kind_for(route: str) -> str:
    return queue.RAW_REANALYSIS if route == "pipeline" else queue.PROCESSED_REANALYSIS


async def _targets(session, plan) -> list:
    from sqlalchemy import select

    from app.models.comparison_target import ComparisonTarget

    return list(
        (
            await session.execute(
                select(ComparisonTarget)
                .where(ComparisonTarget.reproduction_plan_id == plan.id)
                .order_by(ComparisonTarget.id)
            )
        )
        .scalars()
        .all()
    )


async def approve_workflow_checks(session, study, plan, *, route: str) -> dict | None:
    """Record the approved set for this approval. Returns ``evidence["approval"]``, or None when the plan
    selected no claim (a plan read before claim-first selection is approved as it always was)."""
    current = (plan.analysis_selection_json or {}).get("current") or {}
    index = current.get("claim_index")
    targets = await _targets(session, plan)
    if not isinstance(index, int) or not 0 <= index < len(targets):
        return None
    kind = _kind_for(route)
    selected = targets[index]
    analysis = {
        "route": route,
        "workflow": plan.pipeline_key,
        "workflow_version": plan.pipeline_version,
        "contrast_index": selected.contrast_index,
        "experiment": selected.reported_experiment_id,
        "input": current.get("input"),
        "revision": current.get("revision"),
    }
    analysis_key = queue.fingerprint(analysis)[:64]
    covered = [
        t
        for t in targets
        if t.id == selected.id
        or (
            selected.contrast_index is not None
            and t.contrast_index == selected.contrast_index
            and t.reported_experiment_id == selected.reported_experiment_id
            and ((t.checks or {}).get(kind) or {}).get("status") == "available"
        )
    ]
    approval_id = uuid.uuid4().hex
    check_ids = []
    for target in covered:
        record = await queue.ensure_record(
            session,
            study,
            plan,
            target,
            kind,
            {"analysis": analysis, "claim": target.id, "selected": target.id == selected.id},
            analysis_key=analysis_key,
        )
        check_ids.append(record.check_id)
    await queue.approve(session, study.id, check_ids, approval_id=approval_id)
    return {
        "approval_id": approval_id,
        "route": route,
        "checks": check_ids,
        "analysis_key": analysis_key,
        "selected_check": queue.check_id_for(plan.id, selected.id, kind),
        "at": datetime.now(timezone.utc).isoformat(),
    }


async def unapproved_workflow_checks(session, study, plan) -> list:
    """The workflow check records on this plan that no approval covers."""
    return [
        r
        for r in await queue.records_for(session, study.id)
        if r.reproduction_plan_id == plan.id
        and r.kind in queue.WORKFLOW_KINDS
        and not r.approval_id
        and r.state == queue.PENDING
    ]


async def conclude_workflow_checks(session, study, plan) -> None:
    """At conclusion, each approved record gets its outcome from what the run produced."""
    evidence = study.evidence_json or {}
    approval = evidence.get("approval") or {}
    if not approval.get("approval_id"):
        return
    result = evidence.get("level3_result")
    reference = (
        f"run:{study.analysis_run_id}"
        if getattr(study, "analysis_run_id", None)
        else (f"level3_session:{evidence['level3_run_session_id']}" if evidence.get("level3_run_session_id") else None)
    )
    completion = evidence.get("completion") or {}
    governing = next((row for row in completion.get("limitations") or [] if row.get("governs")), None)
    for record in await queue.records_for(session, study.id):
        if record.approval_id != approval["approval_id"] or record.state not in (queue.PENDING, queue.RUNNING):
            continue
        selected = record.check_id == approval.get("selected_check")
        if result and selected:
            await queue.finish(
                session,
                record,
                state=queue.DONE,
                outcome={
                    "outcome": "compared",
                    "verdict": (result.get("concordance") or {}).get("verdict") or result.get("verdict"),
                    "evidence": ["level3_result"],
                    "execution_ref": reference,
                },
            )
        elif result:
            await queue.finish(
                session,
                record,
                state=queue.UNRESOLVED,
                outcome={"outcome": "unresolved", "reason": SHARED_PREDICATE_REASON, "execution_ref": reference},
            )
        else:
            reason = (governing or {}).get("detail") or (governing or {}).get("label") or study.failure_reason
            await queue.finish(
                session,
                record,
                state=queue.BLOCKED if (governing or {}).get("kind") == "controlled_access" else queue.UNRESOLVED,
                outcome={"outcome": "not_executed", "reason": reason, "limitation": (governing or {}).get("kind")},
            )


async def assert_covered(session, study, plan) -> None:
    """D4: raise ``NotApproved`` unless the approval this study carries covers its selected workflow check.
    A study approved before approved sets existed carries none, and runs as it always did."""
    approval = (study.evidence_json or {}).get("approval") or {}
    if not approval.get("approval_id") or plan is None:
        return
    for record in await queue.records_for(session, study.id):
        if record.check_id == approval.get("selected_check") and record.approval_id == approval["approval_id"]:
            return
    raise queue.NotApproved("the selected analysis is not covered by this study's approval")
