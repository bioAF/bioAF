"""plan_8_1 section 3.2: every executed or pending check is a durable, revisioned record.

- A record's ``check_id`` is stable (the plan, the claim, the check kind) through attempts and revisions.
- A dependency change supersedes only the records whose fingerprint includes it; each superseded
  revision is kept whole in history, and the record becomes pending again.
- Each attempt keeps its start, end, outcome, error and execution reference.
- On restart a running record with an execution reference is reconciled against that execution; one
  without becomes interrupted and is queued again.
- A workflow launch carries an idempotency key made of the analysis key and the approval: a retry or a
  restart never launches a second execution, and checks sharing an analysis key share one execution.
- No workflow check runs outside an approved set.
"""

import pytest

from app.services import validation_check_queue as queue
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_study_service import ValidationStudyService


async def _plan(session, user, claims=3):
    study = await ValidationStudyService.create_study(session, user.organization_id, user.id)
    plan = await ReproductionPlanService.create_plan(session, study, user.id, accessions=[])
    targets = await ReproductionPlanService.add_comparison_targets(
        session, plan, [{"metric_key": "", "claim_text": f"claim {i}", "contrast_index": i % 2} for i in range(claims)]
    )
    await session.flush()
    return study, plan, targets


def _deps(**overrides):
    deps = {"predicate": "KO vs WT, P < 0.01, up", "input": {"source": "table.txt", "checksum": None}}
    deps.update(overrides)
    return deps


class TestTheRecord:
    @pytest.mark.asyncio
    async def test_a_record_has_a_stable_check_id_and_starts_pending(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        assert record.check_id == f"plan:{plan.id}:claim:{targets[0].id}:author_results"
        assert (record.state, record.revision) == ("pending", 1)
        again = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        assert again.id == record.id and again.revision == 1

    @pytest.mark.asyncio
    async def test_two_contrasts_keep_separate_records(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        a = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        b = await queue.ensure_record(session, study, plan, targets[1], queue.AUTHOR_RESULTS, _deps())
        await queue.finish(session, a, state=queue.DONE, outcome={"outcome": "agree"})
        await queue.finish(session, b, state=queue.DONE, outcome={"outcome": "disagree"})
        records = {
            r.comparison_target_id: r.outcome_json["outcome"] for r in await queue.records_for(session, study.id)
        }
        assert records == {targets[0].id: "agree", targets[1].id: "disagree"}

    @pytest.mark.asyncio
    async def test_a_dependency_change_supersedes_that_record_and_keeps_its_revision_whole(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        await queue.finish(session, record, state=queue.DONE, outcome={"outcome": "agree"})
        changed = await queue.ensure_record(
            session,
            study,
            plan,
            targets[0],
            queue.AUTHOR_RESULTS,
            _deps(input={"source": "other.txt", "checksum": None}),
        )
        assert (changed.revision, changed.state, changed.outcome_json) == (2, "pending", None)
        (previous,) = changed.history_json
        assert previous["revision"] == 1
        assert previous["outcome"] == {"outcome": "agree"}
        assert previous["state"] == "superseded"

    @pytest.mark.asyncio
    async def test_changing_one_input_invalidates_only_its_dependents(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        on_mapping = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, _deps(sample_mapping={"KO": ["a"]})
        )
        unrelated = await queue.ensure_record(session, study, plan, targets[1], queue.AUTHOR_RESULTS, _deps())
        for record in (on_mapping, unrelated):
            await queue.finish(session, record, state=queue.DONE, outcome={"outcome": "agree"})
        moved = await queue.invalidate(session, study.id, "sample_mapping", {"KO": ["b"]})
        assert [r.id for r in moved] == [on_mapping.id]
        assert on_mapping.state == "pending" and on_mapping.revision == 2
        assert unrelated.state == "done" and unrelated.revision == 1


class TestAttemptsAndRestart:
    @pytest.mark.asyncio
    async def test_each_attempt_is_kept(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        await queue.start(session, record)
        await queue.finish(session, record, state=queue.UNRESOLVED, outcome={"outcome": "unresolved"}, error="404")
        await queue.start(session, record)
        await queue.finish(session, record, state=queue.DONE, outcome={"outcome": "agree"})
        assert [a["outcome"] for a in record.attempts_json] == ["unresolved", "done"]
        assert record.attempts_json[0]["error"] == "404"
        assert all(a["started_at"] and a["finished_at"] for a in record.attempts_json)

    @pytest.mark.asyncio
    async def test_a_running_record_without_an_execution_is_interrupted_and_queued_again(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(session, study, plan, targets[0], queue.AUTHOR_RESULTS, _deps())
        await queue.start(session, record)
        await queue.reconcile(session, study.id, execution_state=lambda ref: None)
        assert record.state == "pending"
        assert record.attempts_json[-1]["outcome"] == "interrupted"

    @pytest.mark.asyncio
    async def test_a_running_record_with_an_execution_is_reconciled_against_it(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, _deps(), analysis_key="k1"
        )
        await queue.approve(session, study.id, [record.check_id], approval_id="appr-1")
        await queue.start(session, record, execution_ref="run:42")
        await queue.reconcile(session, study.id, execution_state=lambda ref: "running" if ref == "run:42" else None)
        assert record.state == "running"
        assert record.attempts_json[-1]["execution_ref"] == "run:42"


class TestWorkflowExecution:
    @pytest.mark.asyncio
    async def test_no_workflow_check_runs_outside_an_approved_set(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(
            session, study, plan, targets[0], queue.RAW_REANALYSIS, _deps(), analysis_key="k1"
        )
        launches = []

        async def launch():
            launches.append(1)
            return "run:1"

        with pytest.raises(queue.NotApproved):
            await queue.execute(session, record, launch=launch)
        assert launches == []

    @pytest.mark.asyncio
    async def test_a_retry_or_restart_launches_no_second_execution(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        record = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, _deps(), analysis_key="k1"
        )
        await queue.approve(session, study.id, [record.check_id], approval_id="appr-1")
        launches = []

        async def launch():
            launches.append(1)
            return f"run:{len(launches)}"

        first = await queue.execute(session, record, launch=launch)
        second = await queue.execute(session, record, launch=launch)
        assert first == second == "run:1"
        assert launches == [1]
        assert queue.launch_key("k1", "appr-1") == record.attempts_json[-1]["launch_key"]

    @pytest.mark.asyncio
    async def test_two_checks_with_the_same_analysis_key_share_one_execution(self, session, admin_user):
        study, plan, targets = await _plan(session, admin_user)
        a = await queue.ensure_record(
            session, study, plan, targets[0], queue.PROCESSED_REANALYSIS, _deps(), analysis_key="k"
        )
        b = await queue.ensure_record(
            session, study, plan, targets[2], queue.PROCESSED_REANALYSIS, _deps(), analysis_key="k"
        )
        await queue.approve(session, study.id, [a.check_id, b.check_id], approval_id="appr-1")
        launches = []

        async def launch():
            launches.append(1)
            return "run:7"

        assert await queue.execute(session, a, launch=launch) == "run:7"
        assert await queue.execute(session, b, launch=launch) == "run:7"
        assert launches == [1]
        assert b.attempts_json[-1]["shared_with"] == a.check_id
