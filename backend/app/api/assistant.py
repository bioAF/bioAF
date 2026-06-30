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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.assistant import (
    AssistantActionPlan,
    AssistantConversation,
    AssistantMessage,
    AssistantToolInvocation,
)
from app.models.organization import Organization
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services import audit_service, llm_provider_config_service, role_service
from app.services.assistant_availability_service import AssistantAvailabilityService
from app.services.assistant_loop_service import AssistantLoopService
from app.services.assistant_tool_catalog import get_tool
from app.services.pipeline_run_service import PipelineRunService

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
    # True if the confirmed action actually ran. Mutating tools (install/import) always run on
    # confirm. A spend action (launch_run) runs only when the org's assistant_launch_enabled toggle
    # is ON; otherwise it stays build-only (the fully-formed request is returned but no run starts).
    executed: bool = False
    # The action result: the launched-run summary, the built launch request (toggle off), or the
    # mutating tool's output.
    result: dict | None = None
    # The id of the PipelineRun created when a spend step actually launched (toggle on); else None.
    run_id: int | None = None


class ConversationSummary(BaseModel):
    id: int
    title: str | None = None
    # First user message, truncated: a readable label for the history list when title is unset.
    preview: str | None = None
    status: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    total: int


class TranscriptMessage(BaseModel):
    id: int
    role: str  # user | assistant | tool
    content: str | None = None
    tool_calls: list | None = None
    created_at: datetime


class TranscriptPlan(BaseModel):
    id: int
    steps: list | None = None
    status: str  # proposed | approved | declined | executed | failed
    created_at: datetime


class ConversationTranscriptResponse(BaseModel):
    id: int
    title: str | None = None
    messages: list[TranscriptMessage]
    plans: list[TranscriptPlan]


class AssistantSettingsResponse(BaseModel):
    # Whether the org has opted in to letting the assistant actually launch runs on confirm.
    launch_enabled: bool


class AssistantSettingsUpdateRequest(BaseModel):
    launch_enabled: bool


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    availability = await AssistantAvailabilityService.get_availability(session, org_id)
    return AvailabilityResponse(enabled=availability.enabled, reason=availability.reason)


@router.get("/settings", response_model=AssistantSettingsResponse)
async def get_assistant_settings(
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    """Read the org's assistant settings. Anyone who can use the assistant may see whether confirmed
    launches actually start runs (so the UI can show the mode)."""
    org_id = int(current_user["org_id"])
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    return AssistantSettingsResponse(launch_enabled=bool(org.assistant_launch_enabled))


@router.put("/settings", response_model=AssistantSettingsResponse)
async def update_assistant_settings(
    data: AssistantSettingsUpdateRequest,
    current_user: dict = require_permission("settings", "configure"),
    session: AsyncSession = Depends(get_session),
):
    """Toggle whether the assistant launches for real on confirm (admin-only org setting). Enabling
    this lets confirmed plans spend compute through the agent, so it is gated on settings:configure."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    previous = {"launch_enabled": bool(org.assistant_launch_enabled)}
    org.assistant_launch_enabled = data.launch_enabled
    await session.flush()
    await audit_service.log_action(
        session,
        user_id,
        "organization",
        org_id,
        "assistant.settings.update",
        details={"launch_enabled": data.launch_enabled},
        previous_value=previous,
    )
    await session.commit()
    return AssistantSettingsResponse(launch_enabled=bool(org.assistant_launch_enabled))


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


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    page: int = 1,
    page_size: int = 50,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    """List the current user's conversations (most recently active first) so they can revisit or
    resume a past chat. Scoped to the caller: a user never sees another user's conversations."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    page_size = min(max(page_size, 1), 100)

    # Per-conversation message count + last-activity time, so the list orders by real recency
    # (the conversation row's updated_at does not change when a message is appended).
    activity = (
        select(
            AssistantMessage.conversation_id.label("cid"),
            func.max(AssistantMessage.created_at).label("last_at"),
            func.count().label("cnt"),
        )
        .group_by(AssistantMessage.conversation_id)
        .subquery()
    )
    base = (
        select(AssistantConversation, activity.c.last_at, activity.c.cnt)
        .outerjoin(activity, activity.c.cid == AssistantConversation.id)
        .where(
            AssistantConversation.organization_id == org_id,
            AssistantConversation.user_id == user_id,
        )
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(func.coalesce(activity.c.last_at, AssistantConversation.created_at).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    conv_ids = [conv.id for conv, _last, _cnt in rows]
    previews: dict[int, str] = {}
    if conv_ids:
        msgs = (
            await session.execute(
                select(AssistantMessage.conversation_id, AssistantMessage.content)
                .where(AssistantMessage.conversation_id.in_(conv_ids), AssistantMessage.role == "user")
                .order_by(AssistantMessage.conversation_id, AssistantMessage.id)
            )
        ).all()
        for cid, content in msgs:
            if cid not in previews and content:
                previews[cid] = content[:120]

    return ConversationListResponse(
        total=total,
        conversations=[
            ConversationSummary(
                id=conv.id,
                title=conv.title,
                preview=previews.get(conv.id),
                status=conv.status,
                message_count=int(cnt or 0),
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
            for conv, _last, cnt in rows
        ],
    )


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationTranscriptResponse)
async def get_conversation_transcript(
    conversation_id: int,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    """Load a past conversation's transcript (its messages and proposed/approved plans) so the UI can
    re-render it and the user can continue where they left off. Owner-scoped via _owned_conversation."""
    conversation = await _owned_conversation(session, conversation_id, current_user)
    messages = (
        (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation.id)
                .order_by(AssistantMessage.id)
            )
        )
        .scalars()
        .all()
    )
    plans = (
        (
            await session.execute(
                select(AssistantActionPlan)
                .where(AssistantActionPlan.conversation_id == conversation.id)
                .order_by(AssistantActionPlan.id)
            )
        )
        .scalars()
        .all()
    )
    return ConversationTranscriptResponse(
        id=conversation.id,
        title=conversation.title,
        messages=[
            TranscriptMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                tool_calls=m.tool_calls_json,
                created_at=m.created_at,
            )
            for m in messages
        ],
        plans=[TranscriptPlan(id=p.id, steps=p.steps_json, status=p.status, created_at=p.created_at) for p in plans],
    )


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
    org_id = conversation.organization_id

    # Whether this org has opted in to letting the assistant actually launch (admin-only toggle). When
    # OFF (default), a spend step stays build-only: the fully-formed request is returned but no run
    # starts. When ON, a spend step launches for real via the normal PipelineRunService.launch_run.
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
    launch_enabled = bool(org.assistant_launch_enabled)

    # Re-check the underlying permission for every step server-side, then run it. The agent acts
    # as the user: a user who cannot launch cannot confirm a launch.
    steps = plan.steps_json or []
    new_steps: list[dict] = []
    result: dict | None = None
    executed = False
    run_id: int | None = None
    for step in steps:
        tool = get_tool(step["tool"])
        if tool is None:
            raise HTTPException(400, f"unknown tool in plan: {step['tool']}")
        resource, action = tool.permission
        if not await role_service.has_permission(session, role_id, resource, action):
            raise HTTPException(403, f"permission denied for {resource}:{action}")
        # A mutating handler runs the real action (install, import) now that the user has confirmed.
        # A spend step (launch_run) launches for real when the org toggle is ON; otherwise the handler
        # only BUILDS the request (no run starts).
        try:
            if tool.consequence_class == "spend" and launch_enabled:
                # Build the request shape (folds accessions into parameters), then launch it for real
                # through the canonical launch path, exactly as the UI's POST /api/pipeline-runs does.
                built = await tool.handler(session, org_id=org_id, user_id=user_id, arguments=step["args"])
                run = await PipelineRunService.launch_run(
                    session,
                    org_id,
                    user_id,
                    PipelineRunLaunchRequest(
                        pipeline_key=built["pipeline_key"],
                        experiment_id=built["experiment_id"],
                        # Scope to the samples the agent selected; without this the launch path runs
                        # against EVERY sample in the experiment and fails when any lack linked files.
                        sample_ids=built.get("sample_ids"),
                        parameters=built.get("parameters") or {},
                        reference_genome=built.get("reference_genome"),
                    ),
                )
                output = {
                    "launched": True,
                    "run_id": run.id,
                    "status": run.status,
                    "pipeline_key": built["pipeline_key"],
                    "experiment_id": built["experiment_id"],
                }
                executed = True
                run_id = run.id
            else:
                output = await tool.handler(session, org_id=org_id, user_id=user_id, arguments=step["args"])
                if tool.consequence_class != "spend":
                    executed = True
        except HTTPException:
            raise
        except Exception as exc:  # surface a failed action as a 400, not a 500
            raise HTTPException(400, f"action '{step['tool']}' failed: {exc}") from exc
        result = output
        new_steps.append({**step, "result": output})

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
        details={
            "via_assistant": True,
            "conversation_id": conversation.id,
            "executed": executed,
            "run_id": run_id,
        },
    )
    await session.commit()

    # Mutating actions have run. A spend action (launch_run) launched a real run when the org toggle
    # is ON (run_id set); otherwise it was built but not started.
    return ConfirmResponse(status="approved", plan_id=plan.id, executed=executed, result=result, run_id=run_id)
