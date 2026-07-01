"""Tests for the assistant conversation data model (spec-02, ai_pipeline_run Phase 1).

Four persisted entities back the conversational assistant: a Conversation (session), its
Messages, the ToolInvocations the agent makes (the unit the enforcement wrapper acts on),
and an ActionPlan presented at the plan-then-confirm gate. These tests assert the entities
persist, carry the org/user for the permission + audit boundary, and default their state
fields per spec-02.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.assistant import (
    AssistantActionPlan,
    AssistantConversation,
    AssistantMessage,
    AssistantToolInvocation,
)

pytestmark = pytest.mark.asyncio


async def _conversation(session, admin_user, **overrides):
    conv = AssistantConversation(
        organization_id=admin_user.organization_id,
        user_id=admin_user.id,
        title="run differential expression on experiment 7",
        provider="anthropic",
        model="claude-opus-4-8",
        **overrides,
    )
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def test_conversation_defaults(session, admin_user):
    conv = await _conversation(session, admin_user)
    await session.refresh(conv)
    assert conv.id is not None
    assert conv.uuid is not None
    assert conv.status == "active"
    assert conv.organization_id == admin_user.organization_id
    assert conv.user_id == admin_user.id


async def test_message_links_to_conversation(session, admin_user):
    conv = await _conversation(session, admin_user)
    msg = AssistantMessage(
        conversation_id=conv.id,
        role="user",
        content="run differential expression on experiment 7",
    )
    session.add(msg)
    await session.flush()
    await session.commit()

    fetched = (
        await session.execute(
            select(AssistantConversation)
            .options(selectinload(AssistantConversation.messages))
            .where(AssistantConversation.id == conv.id)
        )
    ).scalar_one()
    assert [m.id for m in fetched.messages] == [msg.id]
    assert fetched.messages[0].role == "user"


async def test_tool_invocation_state_fields(session, admin_user):
    conv = await _conversation(session, admin_user)
    ti = AssistantToolInvocation(
        conversation_id=conv.id,
        tool_name="launch_run",
        arguments_json={"experiment_id": 7, "pipeline_key": "nf-core/rnaseq"},
        consequence_class="spend",
        status="awaiting_confirmation",
        requires_confirmation=True,
    )
    session.add(ti)
    await session.flush()
    await session.commit()
    await session.refresh(ti)

    assert ti.consequence_class == "spend"
    assert ti.status == "awaiting_confirmation"
    assert ti.requires_confirmation is True
    assert (ti.arguments_json or {})["pipeline_key"] == "nf-core/rnaseq"


async def test_tool_invocation_defaults(session, admin_user):
    conv = await _conversation(session, admin_user)
    ti = AssistantToolInvocation(
        conversation_id=conv.id,
        tool_name="list_samples",
        consequence_class="read_only",
    )
    session.add(ti)
    await session.flush()
    await session.commit()
    await session.refresh(ti)

    assert ti.status == "proposed"
    assert ti.requires_confirmation is False


async def test_action_plan_persist(session, admin_user):
    conv = await _conversation(session, admin_user)
    plan = AssistantActionPlan(
        conversation_id=conv.id,
        steps_json=[{"tool": "launch_run", "args": {"experiment_id": 7, "pipeline_key": "nf-core/rnaseq"}}],
        estimated_cost=Decimal("12.50"),
    )
    session.add(plan)
    await session.flush()
    await session.commit()
    await session.refresh(plan)

    assert plan.status == "proposed"
    assert (plan.steps_json or [])[0]["tool"] == "launch_run"
    assert plan.estimated_cost == Decimal("12.50")
