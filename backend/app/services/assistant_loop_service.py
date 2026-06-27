"""Agentic loop (L1) for the conversational assistant.

run_turn drives the reason-act cycle for one user turn: persist the user message, ask the
active provider (native tool-calling) what to do, run each proposed tool call through the
enforcement wrapper (AssistantToolService.invoke), feed results back as tool messages, and
repeat until the model returns a final answer or a spend action stops for confirmation. A step
cap bounds runaway loops. Execution is synchronous; the provider call is injectable
(submit_override) so tests need no real provider, exactly like the agent_reviews tests.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import (
    AssistantActionPlan,
    AssistantConversation,
    AssistantMessage,
    AssistantToolInvocation,
)
from app.services import llm_provider_config_service
from app.services.assistant_availability_service import AssistantAvailabilityService
from app.services.assistant_tool_catalog import list_tools
from app.services.assistant_tool_service import AssistantToolService, ToolExecutionResult
from app.services.llm_provider_clients import get_client
from app.services.llm_provider_clients.tool_use import ToolUseResult

SubmitFn = Callable[[list, list], Awaitable[ToolUseResult]]

# Behavioral guidance for the agent. The enforcement wrapper is the real guarantee (tools enforce);
# this only shapes how the model proposes. It tells the model that consequential actions are gated
# behind confirmation, that it should batch dependent consequential steps into one plan (so install +
# launch confirm together, per L3), and that in v1 a launch is BUILT but not executed.
ASSISTANT_SYSTEM_PROMPT = (
    "You are the bioAF assistant. You help a lab scientist discover their data and set up and run "
    "bioinformatics pipelines by calling the provided tools. Use read-only tools (list_experiments, "
    "list_samples, list_pipelines, check_status, recommend_pipeline) freely to resolve exactly which "
    "experiment, sample, or pipeline the user means before acting. Consequential tools (install, "
    "launch_run) are never executed on your say-so: they create a proposed plan that the user must "
    "explicitly confirm, so do not claim an action is done before it is confirmed. When the user wants "
    "to install a pipeline and then run it, propose BOTH the install and the launch_run in the SAME "
    "turn so they are confirmed together as one plan. In this version a confirmed launch is prepared "
    "but not actually started, so describe it as a prepared run request, not a started run. Be concise."
)


@dataclass
class LoopResult:
    status: str  # answered | awaiting_confirmation | step_cap_exceeded | unavailable
    text: str | None = None
    action_plan: AssistantActionPlan | None = None
    tool_invocation: AssistantToolInvocation | None = None
    steps: int = 0
    reason: str | None = None


def _tool_specs() -> list[dict]:
    """Provider-agnostic tool descriptors for the model. The per-provider client translates
    these into its native tool schema."""
    return [{"name": t.name, "description": t.description, "args_schema": t.args_schema} for t in list_tools()]


def _tool_message_content(result: ToolExecutionResult) -> dict:
    if result.status == "succeeded":
        return {"status": "succeeded", "result": result.result}
    return {"status": result.status, "error": result.error}


async def _persist_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    *,
    content: str | None = None,
    tool_calls_json: list | None = None,
    tool_invocation_id: int | None = None,
) -> AssistantMessage:
    message = AssistantMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls_json=tool_calls_json,
        tool_invocation_id=tool_invocation_id,
    )
    session.add(message)
    await session.flush()
    await session.commit()
    return message


async def _serialize_messages(session: AsyncSession, conversation_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.id)
        )
    ).scalars()
    return [
        {
            "role": m.role,
            "content": m.content,
            "tool_calls": m.tool_calls_json,
            "tool_invocation_id": m.tool_invocation_id,
        }
        for m in rows
    ]


class AssistantLoopService:
    MAX_STEPS = 10

    @staticmethod
    async def run_turn(
        session: AsyncSession,
        *,
        conversation: AssistantConversation,
        role_id: int,
        user_text: str,
        submit_override: SubmitFn | None = None,
    ) -> LoopResult:
        # Resolve the provider call. With an override, that IS the provider (tests / future
        # callers). Without one, gate on availability and bind the active provider's native
        # tool-calling entrypoint.
        if submit_override is not None:
            submit_fn: SubmitFn = submit_override
        else:
            availability = await AssistantAvailabilityService.get_availability(session, conversation.organization_id)
            if not availability.enabled:
                return LoopResult(status="unavailable", reason=availability.reason)
            active = await llm_provider_config_service.get_active(session, conversation.organization_id)
            client = get_client(active.provider)

            async def _provider_submit(messages: list, tools: list) -> ToolUseResult:
                return await client.submit_with_tools(
                    messages=messages,
                    tools=tools,
                    model=active.model,
                    api_key=active.api_key,
                    system=ASSISTANT_SYSTEM_PROMPT,
                )

            submit_fn = _provider_submit

        await _persist_message(session, conversation.id, "user", content=user_text)
        tools = _tool_specs()

        steps = 0
        while steps < AssistantLoopService.MAX_STEPS:
            steps += 1
            messages = await _serialize_messages(session, conversation.id)
            turn = await submit_fn(messages, tools)

            if turn.is_final:
                await _persist_message(session, conversation.id, "assistant", content=turn.text)
                return LoopResult(status="answered", text=turn.text, steps=steps)

            assistant_message = await _persist_message(
                session,
                conversation.id,
                "assistant",
                content=turn.text,
                tool_calls_json=[{"tool": c.tool, "args": c.args} for c in turn.tool_calls],
            )

            # Collect every consequential (spend/mutating) call from THIS turn into one plan (L3),
            # rather than stopping at the first. Read calls execute and feed their result back as
            # before. After the turn, if any consequential step was collected, stop for a single
            # confirmation of the whole plan; otherwise continue the reason-act loop.
            plan: AssistantActionPlan | None = None
            last_pending: AssistantToolInvocation | None = None
            for call in turn.tool_calls:
                result = await AssistantToolService.invoke(
                    session,
                    conversation=conversation,
                    role_id=role_id,
                    tool_name=call.tool,
                    arguments=call.args,
                    message_id=assistant_message.id,
                    plan=plan,
                )
                if result.status == "awaiting_confirmation":
                    # No result yet; batch it into the shared plan and keep collecting this turn.
                    plan = result.action_plan
                    last_pending = result.tool_invocation
                    continue
                # Read (or rejected) call: feed the result back so the model can continue / revise.
                await _persist_message(
                    session,
                    conversation.id,
                    "tool",
                    content=json.dumps(_tool_message_content(result)),
                    tool_invocation_id=result.tool_invocation.id if result.tool_invocation else None,
                )

            if plan is not None:
                return LoopResult(
                    status="awaiting_confirmation",
                    action_plan=plan,
                    tool_invocation=last_pending,
                    steps=steps,
                )

        return LoopResult(status="step_cap_exceeded", steps=steps)
