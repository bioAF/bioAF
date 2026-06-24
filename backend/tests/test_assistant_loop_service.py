"""Tests for the agentic loop (L1): AssistantLoopService.run_turn.

The loop is the reason-act cycle: it asks the provider (via native tool-calling) what to do,
runs each proposed tool call through the enforcement wrapper, feeds results back, and repeats
until the model produces a final answer or a spend action stops for confirmation. A step cap
is the runaway backstop. Tests drive it with an injected provider call (submit_override), so no
real provider is contacted, exactly like the agent_reviews tests.
"""

import pytest
from sqlalchemy import func, select

from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.experiment import Experiment
from app.models.pipeline_catalog_entry import PipelineCatalogEntry
from app.models.pipeline_run import PipelineRun
from app.models.sample import Sample
from app.services import llm_provider_config_service
from app.services.assistant_loop_service import AssistantLoopService
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult

pytestmark = pytest.mark.asyncio


# ---- Helpers ----


def _scripted(*results):
    """A submit_override that returns each ToolUseResult in turn, clamping to the last."""
    state = {"i": 0}

    async def _submit(messages, tools):
        i = state["i"]
        state["i"] += 1
        return results[min(i, len(results) - 1)]

    return _submit


async def _conversation(session, user):
    conv = AssistantConversation(
        organization_id=user.organization_id,
        user_id=user.id,
        title="t",
        provider="anthropic",
        model="claude-opus-4-8",
    )
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def _bulk_mouse_experiment(session, user):
    exp = Experiment(
        organization_id=user.organization_id,
        name="Bulk mouse",
        owner_user_id=user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    session.add(
        Sample(
            experiment_id=exp.id,
            external_id="B1",
            organism="Mus musculus",
            molecule_type="total RNA",
            library_prep_method="TruSeq Stranded mRNA",
        )
    )
    session.add(
        PipelineCatalogEntry(
            organization_id=user.organization_id,
            pipeline_key="nf-core/rnaseq",
            name="nf-core/rnaseq",
            source_type="github",
            version="3.14.0",
            default_params_json={"aligner": "star_salmon"},
            enabled=True,
        )
    )
    await session.flush()
    await session.commit()
    return exp


async def _configure_active_provider(session, org_id, user_id, *, provider):
    await llm_provider_config_service.upsert(
        session,
        org_id=org_id,
        provider=provider,
        api_key="sk-test-LAST5" if provider != "gemma" else None,
        model=f"{provider}-test-model",
        actor_user_id=user_id,
    )
    await llm_provider_config_service.set_active(session, org_id=org_id, provider=provider, actor_user_id=user_id)
    await session.commit()


async def _message_count(session, conv_id):
    return (
        await session.execute(
            select(func.count()).select_from(AssistantMessage).where(AssistantMessage.conversation_id == conv_id)
        )
    ).scalar_one()


# ---- Tests ----


async def test_loop_runs_read_tool_then_returns_final_answer(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    override = _scripted(
        ToolUseResult(tool_calls=[ToolCall(tool="recommend_pipeline", args={"experiment_id": exp.id})]),
        ToolUseResult(text="I recommend nf-core/rnaseq with the GRCm39 reference."),
    )

    result = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="what pipeline should I run on experiment 7?",
        submit_override=override,
    )

    assert result.status == "answered"
    assert "rnaseq" in result.text
    # The turn is persisted: at least a user message and the final assistant message.
    assert await _message_count(session, conv.id) >= 2


async def test_loop_stops_at_spend_confirmation_without_executing(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    override = _scripted(
        ToolUseResult(tool_calls=[ToolCall(tool="recommend_pipeline", args={"experiment_id": exp.id})]),
        ToolUseResult(
            tool_calls=[
                ToolCall(
                    tool="launch_run",
                    args={
                        "experiment_id": exp.id,
                        "pipeline_key": "nf-core/rnaseq",
                        "parameters": {"aligner": "star_salmon"},
                        "reference_genome": "GRCm39",
                    },
                )
            ]
        ),
    )

    result = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="run differential expression on experiment 7",
        submit_override=override,
    )

    assert result.status == "awaiting_confirmation"
    assert result.action_plan is not None
    assert result.action_plan.status == "proposed"
    # Load-bearing: nothing executed.
    run_count = (await session.execute(select(func.count()).select_from(PipelineRun))).scalar_one()
    assert run_count == 0


async def test_loop_enforces_step_cap(session, admin_user):
    exp = await _bulk_mouse_experiment(session, admin_user)
    conv = await _conversation(session, admin_user)

    # Always asks for another read tool call: it would loop forever without the cap.
    override = _scripted(
        ToolUseResult(tool_calls=[ToolCall(tool="recommend_pipeline", args={"experiment_id": exp.id})])
    )

    result = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="keep going",
        submit_override=override,
    )

    assert result.status == "step_cap_exceeded"
    assert result.steps == AssistantLoopService.MAX_STEPS


async def test_loop_unavailable_when_provider_not_tool_capable(session, admin_user):
    conv = await _conversation(session, admin_user)
    await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="gemma")

    # No override: the loop resolves the active provider and finds it is not tool-capable.
    result = await AssistantLoopService.run_turn(
        session,
        conversation=conv,
        role_id=admin_user.role_id,
        user_text="hello",
    )

    assert result.status == "unavailable"
    assert result.reason is not None
