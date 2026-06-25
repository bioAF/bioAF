"""Assistant API (ai_pipeline_run): availability, conversations, messages, confirm.

The conversational assistant's HTTP surface. Starting a conversation and sending a message
require `assistant:use`; the loop runs synchronously within the message request and returns
either the assistant's answer or a plan awaiting confirmation. Confirming a plan re-checks the
underlying tool's permission server-side (tools enforce, the LLM proposes). In v1 confirm
builds the fully-formed launch request and STOPS: it never POSTs a pipeline run.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.assistant import AssistantActionPlan, AssistantConversation, AssistantToolInvocation
from app.services import audit_service, llm_provider_config_service, role_service
from app.services.assistant_availability_service import AssistantAvailabilityService
from app.services.assistant_loop_service import AssistantLoopService
from app.services.assistant_tool_catalog import get_tool

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AvailabilityResponse(BaseModel):
    enabled: bool
    reason: str | None = None


class CreateConversationRequest(BaseModel):
    title: str | None = None


class ConversationResponse(BaseModel):
    id: int
    status: str
    provider: str | None = None
    model: str | None = None


class MessageRequest(BaseModel):
    text: str


class MessageResponse(BaseModel):
    status: str  # answered | awaiting_confirmation | step_cap_exceeded | unavailable
    text: str | None = None
    action_plan_id: int | None = None
    # The proposed steps ([{tool, args}]) when status is awaiting_confirmation, so the client can
    # render exactly what it is about to confirm (resolved entity, pipeline, params) before spend.
    plan_steps: list[dict] | None = None
    reason: str | None = None


class ConfirmResponse(BaseModel):
    status: str
    plan_id: int
    launch_request: dict | None = None


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    availability = await AssistantAvailabilityService.get_availability(session, org_id)
    return AvailabilityResponse(enabled=availability.enabled, reason=availability.reason)


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    data: CreateConversationRequest,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    active = await llm_provider_config_service.get_active(session, org_id)
    conversation = AssistantConversation(
        organization_id=org_id,
        user_id=user_id,
        title=data.title,
        status="active",
        provider=active.provider if active else None,
        model=active.model if active else None,
    )
    session.add(conversation)
    await session.flush()
    await session.commit()
    return ConversationResponse(
        id=conversation.id,
        status=conversation.status,
        provider=conversation.provider,
        model=conversation.model,
    )


async def _owned_conversation(session: AsyncSession, conversation_id: int, current_user: dict) -> AssistantConversation:
    conversation = (
        await session.execute(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.organization_id == int(current_user["org_id"]),
                AssistantConversation.user_id == int(current_user["sub"]),
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "conversation not found")
    return conversation


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: int,
    data: MessageRequest,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    conversation = await _owned_conversation(session, conversation_id, current_user)
    result = await AssistantLoopService.run_turn(
        session,
        conversation=conversation,
        role_id=int(current_user["role_id"]),
        user_text=data.text,
    )
    return MessageResponse(
        status=result.status,
        text=result.text,
        action_plan_id=result.action_plan.id if result.action_plan else None,
        plan_steps=result.action_plan.steps_json if result.action_plan else None,
        reason=result.reason,
    )


@router.post("/action-plans/{plan_id}/confirm", response_model=ConfirmResponse)
async def confirm_action_plan(
    plan_id: int,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    plan = (
        await session.execute(select(AssistantActionPlan).where(AssistantActionPlan.id == plan_id))
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, "action plan not found")
    conversation = await _owned_conversation(session, plan.conversation_id, current_user)
    if plan.status != "proposed":
        raise HTTPException(409, f"action plan is not pending (status={plan.status})")

    role_id = int(current_user["role_id"])
    user_id = int(current_user["sub"])

    # Re-check the underlying permission for every step server-side, then build each request.
    # The agent acts as the user: a user who cannot launch cannot confirm a launch.
    steps = plan.steps_json or []
    new_steps: list[dict] = []
    launch_request: dict | None = None
    for step in steps:
        tool = get_tool(step["tool"])
        if tool is None:
            raise HTTPException(400, f"unknown tool in plan: {step['tool']}")
        resource, action = tool.permission
        if not await role_service.has_permission(session, role_id, resource, action):
            raise HTTPException(403, f"permission denied for {resource}:{action}")
        built = await tool.handler(
            session, org_id=conversation.organization_id, user_id=user_id, arguments=step["args"]
        )
        launch_request = built
        new_steps.append({**step, "launch_request": built})

    now = datetime.now(timezone.utc)
    plan.steps_json = new_steps
    plan.status = "approved"
    plan.approved_by_user_id = user_id
    plan.approved_at = now

    # Mark the pending spend invocation(s) for this conversation confirmed.
    pending = (
        await session.execute(
            select(AssistantToolInvocation).where(
                AssistantToolInvocation.conversation_id == conversation.id,
                AssistantToolInvocation.status == "awaiting_confirmation",
            )
        )
    ).scalars()
    for invocation in pending:
        invocation.status = "confirmed"
        invocation.confirmed_by_user_id = user_id
        invocation.confirmed_at = now

    await session.flush()
    await audit_service.log_action(
        session,
        user_id,
        "assistant_action_plan",
        plan.id,
        "assistant.plan.confirm",
        details={"via_assistant": True, "conversation_id": conversation.id, "executed": False},
    )
    await session.commit()

    # v1 records the confirmed, fully-formed launch request and STOPS (does not POST a run).
    return ConfirmResponse(status="approved", plan_id=plan.id, launch_request=launch_request)
