"""Confirmed plans stay truthful in later provider turns (plan 12.5).

Confirming a plan executes its steps; the actual outputs must be recorded as the results of the
calls that proposed them, so the next chat turn tells the model what ran (including a launch the
compute provider refused) without executing anything again. Confirming one plan resolves only its
own invocations. Each step is durable once it completes, so a later failure cannot erase a step
(or an accepted compute job) that already ran, and the UI's local Cancel is never a server-side
decline or an approval.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from app.models.assistant import AssistantActionPlan, AssistantConversation, AssistantToolInvocation
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.services.assistant_loop_service import AssistantLoopService
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _scripted_capturing(store, *results):
    state = {"i": 0}

    async def _submit(messages, tools):
        store.append(messages)
        i = state["i"]
        state["i"] += 1
        return results[min(i, len(results) - 1)]

    return _submit


async def _conversation(session, user):
    conv = AssistantConversation(organization_id=user.organization_id, user_id=user.id, title="t")
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def _propose(session, user, conv, *calls, text="do it"):
    result = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=user.role_id,
        user_text=text,
        submit_override=_scripted_capturing([], ToolUseResult(tool_calls=list(calls))),
    )
    assert result.status == "awaiting_confirmation"
    return result.action_plan


async def _next_turn_outputs(session, user, conv):
    """Run one more user turn and return {call_id: content} the model was shown."""
    seen: list = []
    await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=user.role_id,
        user_text="what happened?",
        submit_override=_scripted_capturing(seen, ToolUseResult(text="summary")),
    )
    return {m["tool_call_id"]: m["content"] for m in seen[0] if m["role"] == "tool" and m.get("tool_call_id")}


async def _experiments_named(session, name):
    return (
        await session.execute(select(func.count()).select_from(Experiment).where(Experiment.name == name))
    ).scalar_one()


async def _invocation_statuses(session, conv):
    rows = (
        await session.execute(
            select(AssistantToolInvocation)
            .where(AssistantToolInvocation.conversation_id == conv.id)
            .order_by(AssistantToolInvocation.id)
        )
    ).scalars()
    return [(r.tool_name, r.status) for r in rows]


async def test_next_turn_reports_the_confirmed_outcome_without_running_it_again(
    client, session, admin_user, admin_token
):
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session, admin_user, conv, ToolCall(tool="create_experiment", args={"name": "Confirmed exp"}, id="call_create")
    )

    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 200, confirm.text
    created_id = confirm.json()["result"]["experiment_id"]

    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    outputs = await _next_turn_outputs(session, admin_user, conv)

    assert "awaiting_confirmation" not in outputs["call_create"]
    assert str(created_id) in outputs["call_create"]
    assert "Confirmed exp" in outputs["call_create"]
    assert await _experiments_named(session, "Confirmed exp") == 1
    assert await _invocation_statuses(session, conv) == [("create_experiment", "confirmed")]


async def test_a_refused_launch_is_reported_as_not_launched(client, session, admin_user, admin_token):
    exp = Experiment(
        organization_id=admin_user.organization_id, name="Import", owner_user_id=admin_user.id, status="registered"
    )
    session.add(exp)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/fetchngs",
            name="nf-core/fetchngs",
            source_type="nf-core",
            version="1.12.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.commit()
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(
            tool="launch_run",
            args={"experiment_id": exp.id, "pipeline_key": "nf-core/fetchngs", "accessions": ["SRR1"]},
            id="call_launch",
        ),
    )

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=RuntimeError("quota exceeded"))
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 200, confirm.text

    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    outputs = await _next_turn_outputs(session, admin_user, conv)

    assert '"launched": false' in outputs["call_launch"]
    assert '"status": "failed"' in outputs["call_launch"]
    assert (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 1
    adapter.submit_job.assert_awaited_once()


async def test_confirming_one_plan_leaves_another_pending_plan_untouched(client, session, admin_user, admin_token):
    conv = await _conversation(session, admin_user)
    plan_a = await _propose(
        session, admin_user, conv, ToolCall(tool="create_experiment", args={"name": "Plan A"}, id="call_a")
    )
    plan_b = await _propose(
        session, admin_user, conv, ToolCall(tool="create_experiment", args={"name": "Plan B"}, id="call_b")
    )

    confirm = await client.post(f"/api/assistant/action-plans/{plan_a.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 200, confirm.text

    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    assert await _invocation_statuses(session, conv) == [
        ("create_experiment", "confirmed"),
        ("create_experiment", "awaiting_confirmation"),
    ]
    assert (await session.get(AssistantActionPlan, plan_b.id)).status == "proposed"
    assert await _experiments_named(session, "Plan B") == 0

    outputs = await _next_turn_outputs(session, admin_user, conv)
    assert "Plan A" in outputs["call_a"] and "awaiting_confirmation" not in outputs["call_a"]
    assert "awaiting_confirmation" in outputs["call_b"]


async def test_a_failed_step_keeps_the_record_of_steps_that_already_ran(client, session, admin_user, admin_token):
    """Each confirmed step is durable once it completes. When a later step fails, the earlier step's
    effect and result stay recorded, the failed step and any later step are recorded as not done,
    and the plan cannot be confirmed again."""
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(tool="create_experiment", args={"name": "Ran before failure"}, id="call_ok"),
        ToolCall(tool="create_sample", args={"experiment_id": 99_999_999, "external_id": "S1"}, id="call_bad"),
        ToolCall(tool="create_experiment", args={"name": "Never reached"}, id="call_after"),
    )

    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code == 400
    assert confirm.json()["detail"].startswith("action 'create_sample' failed")  # plan 12's body, unchanged

    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    assert await _experiments_named(session, "Ran before failure") == 1
    assert await _experiments_named(session, "Never reached") == 0
    assert (await session.get(AssistantActionPlan, plan.id)).status == "failed"
    assert await _invocation_statuses(session, conv) == [
        ("create_experiment", "confirmed"),
        ("create_sample", "failed"),
        ("create_experiment", "failed"),
    ]
    outputs = await _next_turn_outputs(session, admin_user, conv)
    assert "Ran before failure" in outputs["call_ok"] and '"executed"' in outputs["call_ok"]
    assert '"failed"' in outputs["call_bad"]
    assert '"not_run"' in outputs["call_after"]
    assert not any("awaiting_confirmation" in outputs[c] for c in ("call_ok", "call_bad", "call_after"))

    again = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert again.status_code == 409
    assert await _experiments_named(session, "Ran before failure") == 1


async def test_an_accepted_compute_job_survives_a_later_step_failure(client, session, admin_user, admin_token):
    """The compute provider accepted the job before a later step failed. The run and its result stay
    recorded, history says it launched, and reconfirming cannot submit it a second time."""
    from app.adapters.models import JobSubmitResult

    exp = Experiment(
        organization_id=admin_user.organization_id, name="Accepted", owner_user_id=admin_user.id, status="registered"
    )
    session.add(exp)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/fetchngs",
            name="nf-core/fetchngs",
            source_type="nf-core",
            version="1.12.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.commit()
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(
            tool="launch_run",
            args={"experiment_id": exp.id, "pipeline_key": "nf-core/fetchngs", "accessions": ["SRR1"]},
            id="call_launch",
        ),
        ToolCall(tool="create_sample", args={"experiment_id": 99_999_999, "external_id": "X"}, id="call_bad"),
    )

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(return_value=JobSubmitResult(job_id="accepted-job", status="running"))
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        first = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
        again = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))

    assert first.status_code == 400
    assert again.status_code == 409
    adapter.submit_job.assert_awaited_once()
    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    assert (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one() == 1
    outputs = await _next_turn_outputs(session, admin_user, conv)
    assert '"launched": true' in outputs["call_launch"]
    assert "awaiting_confirmation" not in outputs["call_launch"]


async def test_a_denied_later_step_stops_the_plan_before_anything_runs(
    client, session, admin_user, bench_user, bench_token
):
    """Permissions for every step are checked before any step executes. Proposal already rejects a
    tool the user cannot run, so this models a permission that changed after the plan was proposed."""
    conv = await _conversation(session, bench_user)
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[
            {"tool": "create_experiment", "args": {"name": "Bench first step"}, "consequence_class": "mutating"},
            {
                "tool": "launch_run",
                "args": {"experiment_id": 1, "pipeline_key": "nf-core/rnaseq"},
                "consequence_class": "spend",
            },
        ],
        status="proposed",
    )
    session.add(plan)
    await session.commit()
    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(bench_token))
    assert confirm.status_code == 403
    session.expunge_all()
    assert await _experiments_named(session, "Bench first step") == 0
    assert (await session.get(AssistantActionPlan, plan.id)).status == "proposed"


async def test_unconfirmed_plan_stays_pending_and_unexecuted_across_turns(session, admin_user):
    """The UI's Cancel only dismisses the plan locally; the server keeps it proposed. Later turns
    neither execute it nor treat it as approved or declined."""
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session, admin_user, conv, ToolCall(tool="create_experiment", args={"name": "Cancelled"}, id="call_x")
    )

    for _ in range(2):
        outputs = await _next_turn_outputs(session, admin_user, conv)
        assert "awaiting_confirmation" in outputs["call_x"]

    assert (await session.get(AssistantActionPlan, plan.id)).status == "proposed"
    assert await _experiments_named(session, "Cancelled") == 0
    assert await _invocation_statuses(session, conv) == [("create_experiment", "awaiting_confirmation")]


async def test_concurrent_confirmations_run_the_plan_once(client, session, admin_user, admin_token):
    """Steps commit as they complete, so the plan is claimed before the first step runs: a second
    confirmation arriving mid-execution is refused instead of submitting the job again."""
    import asyncio

    from app.adapters.models import JobSubmitResult

    exp = Experiment(
        organization_id=admin_user.organization_id, name="Once", owner_user_id=admin_user.id, status="registered"
    )
    session.add(exp)
    session.add(
        PipelineCatalogEntry(
            organization_id=admin_user.organization_id,
            pipeline_key="nf-core/fetchngs",
            name="nf-core/fetchngs",
            source_type="nf-core",
            version="1.12.0",
            default_params_json={},
            enabled=True,
        )
    )
    await session.commit()
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(
            tool="launch_run",
            args={"experiment_id": exp.id, "pipeline_key": "nf-core/fetchngs", "accessions": ["SRR1"]},
            id="call_launch",
        ),
    )

    async def slow_submit(*args, **kwargs):
        await asyncio.sleep(0.5)
        return JobSubmitResult(job_id="once-job", status="running")

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=slow_submit)
    url = f"/api/assistant/action-plans/{plan.id}/confirm"
    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter):
        first, second = await asyncio.gather(
            client.post(url, headers=_auth(admin_token)), client.post(url, headers=_auth(admin_token))
        )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    adapter.submit_job.assert_awaited_once()


async def test_an_unexpected_step_failure_returns_a_fixed_message_and_logs_only_its_type(
    client, session, admin_user, admin_token, caplog
):
    """A step's unexpected exception (for example a database error) can carry SQL, parameters, or
    other internals; none of it reaches the response or the log, only the exception type."""
    import logging

    marker = "SYNTH-SECRET [SQL: INSERT INTO samples ...] [parameters: (10, 'P12-A')]"
    exp = Experiment(organization_id=admin_user.organization_id, name="Boom", owner_user_id=admin_user.id)
    session.add(exp)
    await session.commit()
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(tool="create_sample", args={"experiment_id": exp.id, "external_id": "S1"}, id="call_boom"),
    )

    caplog.set_level(logging.DEBUG)
    with patch(
        "app.services.assistant_tool_catalog.SampleService.create_sample", AsyncMock(side_effect=RuntimeError(marker))
    ):
        confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))

    assert confirm.status_code == 400
    assert confirm.json() == {"detail": "action 'create_sample' failed and did not complete."}
    assert "SYNTH-SECRET" not in caplog.text and "INSERT" not in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    session.expunge_all()
    assert (await session.get(AssistantActionPlan, plan.id)).status == "failed"


async def test_a_duplicate_sample_id_is_a_clear_conflict(client, session, admin_user, admin_token):
    exp = Experiment(organization_id=admin_user.organization_id, name="Dupes", owner_user_id=admin_user.id)
    session.add(exp)
    await session.flush()
    from app.models.sample import Sample

    session.add(Sample(experiment_id=exp.id, external_id="P12-A", organism="Homo sapiens"))
    await session.commit()
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(tool="create_sample", args={"experiment_id": exp.id, "external_id": "P12-A"}, id="call_dupe"),
    )

    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))

    assert confirm.status_code == 409
    assert confirm.json()["detail"] == "Sample ID “P12-A” already exists in this experiment."
    assert "SQL" not in confirm.text and "INSERT" not in confirm.text
    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    assert (await session.get(AssistantActionPlan, plan.id)).status == "failed"
    outputs = await _next_turn_outputs(session, admin_user, conv)
    assert '"failed"' in outputs["call_dupe"]
