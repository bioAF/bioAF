"""Confirmed plans stay truthful in later provider turns (plan 12.5).

Confirming a plan executes its steps; the actual outputs must be recorded as the results of the
calls that proposed them, so the next chat turn tells the model what ran (including a launch the
compute provider refused) without executing anything again. Confirming one plan resolves only its
own invocations. A failed confirmation leaves the truthful pre-confirmation state, and the UI's
local Cancel is never a server-side decline or an approval.
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


async def test_a_failed_confirmation_keeps_the_truthful_pending_state(client, session, admin_user, admin_token):
    """Steps run in one transaction: when a later step fails, the earlier step's writes roll back,
    so nothing is recorded as having run and the plan stays pending."""
    conv = await _conversation(session, admin_user)
    plan = await _propose(
        session,
        admin_user,
        conv,
        ToolCall(tool="create_experiment", args={"name": "Rolled back"}, id="call_ok"),
        ToolCall(tool="create_sample", args={"experiment_id": 99_999_999, "external_id": "S1"}, id="call_bad"),
    )

    confirm = await client.post(f"/api/assistant/action-plans/{plan.id}/confirm", headers=_auth(admin_token))
    assert confirm.status_code >= 400

    session.expunge_all()
    conv = await session.get(AssistantConversation, conv.id)
    assert await _experiments_named(session, "Rolled back") == 0
    assert (await session.get(AssistantActionPlan, plan.id)).status == "proposed"
    assert await _invocation_statuses(session, conv) == [
        ("create_experiment", "awaiting_confirmation"),
        ("create_sample", "awaiting_confirmation"),
    ]
    outputs = await _next_turn_outputs(session, admin_user, conv)
    assert all("awaiting_confirmation" in outputs[c] for c in ("call_ok", "call_bad"))


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
