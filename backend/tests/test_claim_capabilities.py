"""plan_8_2 section 2.2: a claim's capabilities follow the evidence, and say which evidence they rest on.

Groff's claims said "no result table is published for this claim's experiment" in one section while
another showed the supplement's comparison: the capabilities were computed once, while the paper was read,
before the supplement was retrieved. They are now refreshed whenever the evidence behind them changes
(a supplement retrieved, a table bound or rejected, a check concluded), each with the revision it rests
on. Capability (a check can be attempted), activity (it is pending or running) and outcome (what it
established) stay separate: a completed unresolved attempt is not an unavailable resource.
"""

import pytest

from app.services import validation_check_queue as queue
from app.services import validation_consistency_checks as consistency
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService
from tests.test_binding_acceptance import _GROFF_CLAIMS, _GROFF_CONTRASTS, _groff_supplements, _refuse


async def _groff_study(session, user, *, supplements):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    study.state = "classified"
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["EGAS00000000001"],
        differential_design={"contrasts": [{**c, "reported_experiment_id": "e1"} for c in _GROFF_CONTRASTS]},
        reported_experiments=[{"id": "e1", "assay": "bulk RNA-seq", "claim_indices": [0, 1, 2, 3]}],
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                **c,
                "metric_key": "",
                "reported_experiment_id": "e1",
                # What the read computed, before any supplement was retrieved.
                "checks": {
                    "author_results": {
                        "status": "unavailable",
                        "reason": "no result table is published for this claim's experiment",
                        "requirement": "result_table",
                    }
                },
            }
            for c in _GROFF_CLAIMS
        ],
    )
    study.evidence_json = {"supplements": supplements}
    await session.flush()
    return study, plan, targets


@pytest.mark.asyncio
async def test_a_retrieved_bound_supplement_updates_every_claims_availability(session, admin_user):
    from app.services.validation_claim_capabilities import refresh_claim_capabilities
    from app.services.validation_report_summary import report_summary_for

    study, plan, targets = await _groff_study(session, admin_user, supplements=await _groff_supplements())
    await consistency.enqueue(session, study, plan)
    await consistency.run_pending(session, study, plan, fetcher=_refuse)
    await refresh_claim_capabilities(session, study, plan)
    await session.flush()
    for target in targets:
        await session.refresh(target)

    sex = targets[0].checks["author_results"]
    assert "Supplemental File S3" in (sex["reason"] or "") or sex["requirement"] == "predicate"
    assert "no result table is published" not in (sex["reason"] or "")
    for target in targets[1:]:
        other = target.checks["author_results"]
        assert other["status"] == "unavailable" and other["requirement"] == "result_table"
        assert "XX vs XY WE" in other["reason"]
    # Each fact names the evidence it rests on.
    assert sex["basis"]["binding_version"] >= 1 and sex["basis"]["evidence"]

    summary = await report_summary_for(session, study, admin_user.organization_id)
    rows = summary["claims"]
    checks = {c["key"]: c for c in rows[1]["checks"]}
    assert "XX vs XY WE" in checks["author_results"]["reason"]


@pytest.mark.asyncio
async def test_a_completed_unresolved_attempt_keeps_the_check_available(session, admin_user):
    """A table bound and read whose comparison came out unresolved: the check could be attempted and was.
    Its capability stays available; the outcome says unresolved."""
    from app.services.validation_claim_capabilities import refresh_claim_capabilities

    study, plan, targets = await _groff_study(session, admin_user, supplements=await _groff_supplements())
    targets[0].cutoffs = [{"kind": "padj", "operator": "<", "value": 0.05}]
    targets[0].direction = "up"
    await session.flush()
    await consistency.enqueue(session, study, plan)
    await consistency.run_pending(session, study, plan, fetcher=_refuse)
    await refresh_claim_capabilities(session, study, plan)
    await session.refresh(targets[0])
    records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
    assert records[targets[0].id].state in (queue.DONE, queue.UNRESOLVED)
    assert targets[0].checks["author_results"]["status"] == "available"
