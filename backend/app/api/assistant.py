"""Assistant API (ai_pipeline_run): availability, conversations, messages, confirm.

The conversational assistant's HTTP surface. Starting a conversation and sending a message
require `assistant:use`; the loop runs synchronously within the message request and returns
either the assistant's answer or a plan awaiting confirmation. Confirming a plan re-checks the
underlying tool's permission server-side (tools enforce, the LLM proposes) and then runs it: a
spend action (launch_run) launches a real PipelineRun. The plan-then-confirm gate is the safety
boundary, and the confirm UI warns when an action will spend compute.
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.exceptions import DomainError
from app.models.assistant import (
    AssistantActionPlan,
    AssistantConversation,
    AssistantMessage,
    AssistantToolInvocation,
)
from app.schemas.pipeline_run import PipelineRunLaunchRequest
from app.services import audit_service, llm_provider_config_service, role_service
from app.services.assistant_availability_service import AssistantAvailabilityService
from app.services.assistant_history import LINK_KEY, link_call, public_plan_steps, public_tool_calls
from app.services.assistant_loop_service import AssistantLoopService
from app.services.assistant_tool_catalog import get_tool
from app.services.llm_provider_clients import ProviderError
from app.services.pipeline_run_service import PipelineRunService

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
logger = logging.getLogger("bioaf.assistant")


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
    # True if the confirmed action actually ran. Every confirmed action runs now: mutating tools
    # (install / create / import) apply their change, and a spend action (launch_run) launches a real
    # PipelineRun. The plan-then-confirm gate is the safety boundary (the user confirmed it).
    executed: bool = False
    # The action result: the launched-run summary or the mutating tool's output.
    result: dict | None = None
    # The id of the PipelineRun created when a spend step launched a run; else None.
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
            for conv, _last, cnt in cast("Sequence[tuple[AssistantConversation, datetime | None, int | None]]", rows)
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
                tool_calls=public_tool_calls(m.tool_calls_json),
                created_at=m.created_at,
            )
            for m in messages
        ],
        plans=[
            TranscriptPlan(id=p.id, steps=public_plan_steps(p.steps_json), status=p.status, created_at=p.created_at)
            for p in plans
        ],
    )


_LLM_SETTINGS = "Settings > Integrations > LLMs"
_PROVIDER_UNAVAILABLE = (502, "The AI provider is temporarily unavailable. Try again in a few minutes.")
# Fixed user-facing text per ProviderError class. The provider's own message is never shown.
_PROVIDER_ERRORS: dict[str, tuple[int, str]] = {
    "auth": (
        502,
        f"The AI provider did not accept bioAF's credentials. An administrator should check the API key in "
        f"{_LLM_SETTINGS}.",
    ),
    "other": (
        502,
        f"The AI provider rejected the assistant's request. An administrator should check the provider and model "
        f"configuration in {_LLM_SETTINGS}.",
    ),
    "rate_limit": (429, "The AI provider is limiting requests right now. Wait a minute and try again."),
    "server": _PROVIDER_UNAVAILABLE,
    "transport": _PROVIDER_UNAVAILABLE,
    "incomplete": (502, "The AI provider stopped before finishing its response. Try again, or ask a shorter question."),
    "parse": (502, "The AI provider returned a response the assistant could not use. Try again."),
}
_PROVIDER_TIMEOUT = (504, "The AI provider did not respond in time. Try again.")


def _provider_http_error(exc: ProviderError, *, conversation_id: int) -> HTTPException:
    """Translate an expected provider failure into a controlled error. Messages, tool results, and
    plans committed earlier in the turn stay as they are; nothing is claimed as answered. Logs carry
    only allowlisted metadata, never the provider's message."""
    logger.warning(
        "assistant provider request failed: conversation_id=%s provider=%s error_class=%s upstream_status=%s "
        "upstream_code=%s timeout=%s",
        conversation_id,
        exc.provider or "unknown",
        exc.error_class,
        exc.upstream_status if exc.upstream_status is not None else "none",
        exc.upstream_code or "none",
        str(exc.timeout).lower(),
    )
    if exc.timeout:
        code, detail = _PROVIDER_TIMEOUT
    else:
        code, detail = _PROVIDER_ERRORS.get(exc.error_class, _PROVIDER_ERRORS["other"])
    return HTTPException(status_code=code, detail=detail)


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: int,
    data: MessageRequest,
    current_user: dict = require_permission("assistant", "use"),
    session: AsyncSession = Depends(get_session),
):
    conversation = await _owned_conversation(session, conversation_id, current_user)
    try:
        result = await AssistantLoopService.run_turn(
            session,
            conversation=conversation,
            role_id=int(current_user["role_id"]),
            user_text=data.text,
        )
    except ProviderError as exc:
        raise _provider_http_error(exc, conversation_id=conversation.id) from exc
    return MessageResponse(
        status=result.status,
        text=result.text,
        action_plan_id=result.action_plan.id if result.action_plan else None,
        plan_steps=public_plan_steps(result.action_plan.steps_json) if result.action_plan else None,
        reason=result.reason,
    )


_FAILED_STEP = {"status": "failed", "note": "This step failed and did not complete."}
_NOT_RUN_STEP = {"status": "not_run", "note": "Not run: an earlier step in the same plan failed."}


async def _record_step_outcome(
    session: AsyncSession,
    conversation_id: int,
    step: dict,
    content: dict,
    *,
    invocation_status: str,
    user_id: int,
    now: datetime,
    output: dict | None = None,
) -> None:
    """Record what happened to one confirmed plan step as the result of the call that proposed it:
    resolve its own invocation and write a linked tool result for the next provider turn."""
    link = step.get(LINK_KEY)
    if not link:
        return  # a plan proposed before call tracking has no call to attach the result to
    invocation = await session.get(AssistantToolInvocation, link.get("invocation_id"))
    if invocation is None or invocation.conversation_id != conversation_id:
        return
    invocation.status = invocation_status
    invocation.confirmed_by_user_id = user_id
    invocation.confirmed_at = now
    if output is not None:
        invocation.result_json = output
    else:
        invocation.error = content["note"]
    tool_message = AssistantMessage(
        conversation_id=conversation_id,
        role="tool",
        content=json.dumps(content, default=str),
        tool_invocation_id=invocation.id,
    )
    session.add(tool_message)
    await session.flush()
    proposing = await session.get(AssistantMessage, link.get("message_id"))
    if proposing is not None and proposing.conversation_id == conversation_id and proposing.tool_calls_json:
        proposing.tool_calls_json = link_call(
            proposing.tool_calls_json, link["call_index"], result_message_id=tool_message.id
        )


async def _run_confirmed_step(session: AsyncSession, tool, step: dict, *, org_id: int, user_id: int) -> dict:
    if tool.consequence_class == "spend":
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
            # Mark the launch audit entry as agent-driven (attribution stays the user).
            via_assistant=True,
        )
        # A run the compute provider refused is still created (and committed with its step), but it
        # was not launched; say so rather than reporting a start.
        output = {
            "launched": run.status != "failed",
            "run_id": run.id,
            "status": run.status,
            "pipeline_key": built["pipeline_key"],
            "experiment_id": built["experiment_id"],
        }
        if run.status == "failed":
            output["error_message"] = run.error_message
        return output
    # A mutating handler applies its change (install, create_experiment/sample, import).
    return await tool.handler(session, org_id=org_id, user_id=user_id, arguments=step["args"])


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

    # Re-check the underlying permission for every step server-side before ANY step runs, so a denied
    # later step cannot strand an earlier one. The agent acts as the user: a user who cannot launch
    # cannot confirm a launch. The confirm itself is the safety gate, so a confirmed spend action
    # (launch_run) launches a real run rather than only building it.
    steps = plan.steps_json or []
    tools = []
    for step in steps:
        tool = get_tool(step["tool"])
        if tool is None:
            raise HTTPException(400, f"unknown tool in plan: {step['tool']}")
        resource, action = tool.permission
        if not await role_service.has_permission(session, role_id, resource, action):
            raise HTTPException(403, f"permission denied for {resource}:{action}")
        tools.append(tool)

    # Claim the plan atomically before anything runs, so a second confirmation arriving while steps
    # execute (and commit) is refused rather than running them again.
    claimed = await session.execute(
        update(AssistantActionPlan)
        .where(AssistantActionPlan.id == plan.id, AssistantActionPlan.status == "proposed")
        .values(status="executing")
    )
    if cast(CursorResult, claimed).rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "action plan is not pending (status=executing)")
    await session.commit()
    await session.refresh(plan)

    # Each step runs in its own savepoint and is committed, with its linked result, as soon as it
    # completes. A later failure therefore cannot erase a step that already ran, including a compute
    # job the provider accepted. On failure the failed step and every later step are recorded as not
    # done, the plan becomes "failed" (so it cannot be confirmed again), and the step's own error is
    # returned unchanged.
    new_steps: list[dict] = []
    result: dict | None = None
    executed = False
    run_id: int | None = None
    failure: Exception | None = None
    for index, (step, tool) in enumerate(zip(steps, tools)):
        now = datetime.now(timezone.utc)
        try:
            async with session.begin_nested():
                output = await _run_confirmed_step(session, tool, step, org_id=org_id, user_id=user_id)
        except (HTTPException, DomainError) as exc:
            failure = exc  # keep the failure's own status and body
        except Exception as exc:  # surface a failed action as a 400, not a 500
            http_error = HTTPException(400, f"action '{step['tool']}' failed: {exc}")
            http_error.__cause__ = exc
            failure = http_error
        if failure is not None:
            await _record_step_outcome(
                session, conversation.id, step, _FAILED_STEP, invocation_status="failed", user_id=user_id, now=now
            )
            new_steps.append({**step, "outcome": "failed"})
            for later in steps[index + 1 :]:
                await _record_step_outcome(
                    session, conversation.id, later, _NOT_RUN_STEP, invocation_status="failed", user_id=user_id, now=now
                )
                new_steps.append({**later, "outcome": "not_run"})
            break
        executed = True
        result = output
        if tool.consequence_class == "spend":
            run_id = output["run_id"]
        new_steps.append({**step, "result": output})
        plan.steps_json = new_steps + steps[index + 1 :]
        await _record_step_outcome(
            session,
            conversation.id,
            step,
            {"status": "executed", "result": output},
            invocation_status="confirmed",
            user_id=user_id,
            now=now,
            output=output,
        )
        await session.commit()

    now = datetime.now(timezone.utc)
    plan.steps_json = new_steps
    plan.status = "failed" if failure is not None else "approved"
    plan.approved_by_user_id = user_id
    plan.approved_at = now
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
            "failed": failure is not None,
        },
    )
    await session.commit()
    if failure is not None:
        raise failure

    # Every confirmed step ran: mutating actions applied, and a spend action (launch_run) launched a
    # real run (run_id set).
    return ConfirmResponse(status="approved", plan_id=plan.id, executed=executed, result=result, run_id=run_id)
