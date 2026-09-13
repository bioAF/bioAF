"""plan_8_1 section 3.2 and D4: one approval covers a specified set of workflow checks.

The approval records, as check records carrying its ``approval_id`` and one ``analysis_key``, the selected
claim's check on the approved route and every claim the same analysis supports (the same contrast and
experiment, the same check available). No workflow check runs outside that set. At conclusion each record
gets its outcome from what the run produced, and the one execution is referenced by every record sharing it.
"""

import pytest

from app.services import validation_check_queue as queue
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService
from app.services.validation_workflow_checks import (
    SHARED_PREDICATE_REASON,
    approve_workflow_checks,
    assert_covered,
    conclude_workflow_checks,
)

_AVAILABLE = {"processed_reanalysis": {"status": "available"}, "raw_reanalysis": {"status": "available"}}


async def _seed(session, user):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(
        session,
        study,
        user.id,
        accessions=["GSE555001"],
        pipeline_key="nf-core/rnaseq",
        analysis_selection={"current": {"claim_index": 0, "contrast_index": 0, "revision": 1}},
    )
    targets = await ReproductionPlanService.add_comparison_targets(
        session,
        plan,
        [
            {
                "metric_key": "",
                "claim_text": "genes up",
                "contrast_index": 0,
                "reported_experiment_id": "e1",
                "checks": _AVAILABLE,
            },
            {
                "metric_key": "",
                "claim_text": "genes down",
                "contrast_index": 0,
                "reported_experiment_id": "e1",
                "checks": _AVAILABLE,
            },
            {
                "metric_key": "",
                "claim_text": "day 7",
                "contrast_index": 1,
                "reported_experiment_id": "e1",
                "checks": _AVAILABLE,
            },
        ],
    )
    await session.flush()
    return study, plan, targets


class TestTheApprovedSet:
    @pytest.mark.asyncio
    async def test_it_holds_the_selected_check_and_the_claims_its_analysis_supports(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        approval = await approve_workflow_checks(session, study, plan, route="deposit")
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert set(records) == {targets[0].id, targets[1].id}
        assert {r.kind for r in records.values()} == {"processed_reanalysis"}
        assert {r.approval_id for r in records.values()} == {approval["approval_id"]}
        assert len({r.analysis_key for r in records.values()}) == 1
        assert approval["selected_check"] == records[targets[0].id].check_id

    @pytest.mark.asyncio
    async def test_the_pipeline_route_approves_raw_reanalysis(self, session, admin_user):
        study, plan, _ = await _seed(session, admin_user)
        await approve_workflow_checks(session, study, plan, route="pipeline")
        assert {r.kind for r in await queue.records_for(session, study.id)} == {"raw_reanalysis"}

    @pytest.mark.asyncio
    async def test_no_workflow_check_runs_outside_the_approved_set(self, session, admin_user):
        study, plan, targets = await _seed(session, admin_user)
        study.evidence_json = {"approval": await approve_workflow_checks(session, study, plan, route="deposit")}
        await assert_covered(session, study, plan)
        # A record the approval did not cover is never launched.
        outside = await queue.ensure_record(
            session, study, plan, targets[2], queue.PROCESSED_REANALYSIS, {"x": 1}, analysis_key="k2"
        )

        async def launch():
            raise AssertionError("launched")

        with pytest.raises(queue.NotApproved):
            await queue.execute(session, outside, launch=launch)
        # An approval whose selected check is gone is refused before anything runs.
        study.evidence_json = {"approval": {**study.evidence_json["approval"], "selected_check": outside.check_id}}
        with pytest.raises(queue.NotApproved):
            await assert_covered(session, study, plan)


class TestAtConclusion:
    @pytest.mark.asyncio
    async def test_the_selected_record_takes_the_result_and_the_shared_one_says_what_it_was_not(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user)
        approval = await approve_workflow_checks(session, study, plan, route="deposit")
        study.evidence_json = {
            "approval": approval,
            "level3_result": {"concordance": {"verdict": "agree"}},
            "level3_run_session_id": 12,
        }
        await conclude_workflow_checks(session, study, plan)
        records = {r.comparison_target_id: r for r in await queue.records_for(session, study.id)}
        assert (records[targets[0].id].state, records[targets[0].id].outcome_json["verdict"]) == ("done", "agree")
        shared = records[targets[1].id]
        assert (shared.state, shared.outcome_json["reason"]) == ("unresolved", SHARED_PREDICATE_REASON)
        assert (
            shared.outcome_json["execution_ref"]
            == records[targets[0].id].outcome_json["execution_ref"]
            == "level3_session:12"
        )

    @pytest.mark.asyncio
    async def test_a_run_that_never_executed_leaves_its_records_with_the_governing_limitation(
        self, session, admin_user
    ):
        study, plan, targets = await _seed(session, admin_user)
        approval = await approve_workflow_checks(session, study, plan, route="pipeline")
        study.evidence_json = {
            "approval": approval,
            "completion": {
                "limitations": [{"kind": "controlled_access", "label": "Controlled access", "governs": True}]
            },
        }
        await conclude_workflow_checks(session, study, plan)
        states = {r.state for r in await queue.records_for(session, study.id)}
        assert states == {"blocked"}
