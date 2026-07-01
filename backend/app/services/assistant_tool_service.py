"""Tool execution + enforcement wrapper (T2): the single choke point.

Implements "tools enforce, the LLM proposes". Every tool call goes through invoke(), which,
in order: looks the tool up in the catalog (T1); checks the caller's RBAC permission for the
underlying action server-side; validates the arguments; then either (spend) creates an
AssistantActionPlan and waits for confirmation WITHOUT executing, or (read / mutating)
executes the handler in the user's context. Every attempt against a real tool is recorded as
an AssistantToolInvocation and written to the audit log, attributed to the user and marked as
taken via the assistant. A rejection is never silently bypassed: it is returned to the loop
as an error so the agent can revise.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import AssistantActionPlan, AssistantConversation, AssistantToolInvocation
from app.services import audit_service, role_service
from app.services.assistant_tool_catalog import ToolDescriptor, get_tool

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "integer": int,
    "number": (int, float),
    "string": str,
    "boolean": bool,
    "object": dict,
    "array": list,
}


@dataclass
class ToolExecutionResult:
    status: str  # succeeded | failed | declined | awaiting_confirmation
    tool_invocation: AssistantToolInvocation | None = None
    # A tool's output (a dict) on success, else None. Typed Any (not Any | None) so callers/tests can
    # index it after checking status; the None case is already subsumed by Any.
    result: Any = None
    error: str | None = None
    action_plan: AssistantActionPlan | None = None


def _validate_args(schema: dict, arguments: dict) -> str | None:
    """Minimal JSON-schema-subset validation: required keys present and declared types match.
    Returns an error string, or None when valid. (jsonschema is not a dependency here, and the
    Phase 1 schemas are simple; swap in a real validator if the schemas grow.)"""
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    for key in schema.get("required", []):
        if key not in arguments or arguments[key] is None:
            return f"missing required argument: {key}"
    for key, spec in schema.get("properties", {}).items():
        if key not in arguments or arguments[key] is None:
            continue
        declared = spec.get("type")
        expected = _TYPE_MAP.get(declared)
        # bool is a subclass of int; reject it where a plain integer/number is expected.
        if declared in ("integer", "number") and isinstance(arguments[key], bool):
            return f"argument {key} must be of type {declared}"
        if expected is not None and not isinstance(arguments[key], expected):
            return f"argument {key} must be of type {declared}"
    return None


def _new_invocation(
    conversation: AssistantConversation,
    message_id: int | None,
    tool: ToolDescriptor,
    arguments: dict,
    *,
    status: str,
    requires_confirmation: bool = False,
    error: str | None = None,
) -> AssistantToolInvocation:
    return AssistantToolInvocation(
        conversation_id=conversation.id,
        message_id=message_id,
        tool_name=tool.name,
        arguments_json=arguments,
        consequence_class=tool.consequence_class,
        status=status,
        requires_confirmation=requires_confirmation,
        error=error,
    )


async def _audit(
    session: AsyncSession,
    user_id: int,
    invocation: AssistantToolInvocation,
    tool: ToolDescriptor,
    conversation: AssistantConversation,
    *,
    outcome: str,
) -> None:
    await audit_service.log_action(
        session,
        user_id,
        "assistant_tool_invocation",
        invocation.id,
        f"assistant.tool.{tool.name}"[:50],
        details={
            "via_assistant": True,
            "conversation_id": conversation.id,
            "tool": tool.name,
            "consequence_class": tool.consequence_class,
            "outcome": outcome,
        },
    )


class AssistantToolService:
    @staticmethod
    async def invoke(
        session: AsyncSession,
        *,
        conversation: AssistantConversation,
        role_id: int,
        tool_name: str,
        arguments: dict,
        message_id: int | None = None,
        plan: AssistantActionPlan | None = None,
    ) -> ToolExecutionResult:
        tool = get_tool(tool_name)
        if tool is None:
            # Hallucinated tool: there is no real capability to record against. Return the error
            # so the loop can correct itself.
            return ToolExecutionResult(status="failed", error=f"unknown tool: {tool_name}")

        org_id = conversation.organization_id
        user_id = conversation.user_id
        resource, action = tool.permission

        # Permission gate: server-side, against the user's role, never the model's claim.
        if not await role_service.has_permission(session, role_id, resource, action):
            return await _record_terminal(
                session,
                conversation,
                message_id,
                tool,
                arguments,
                user_id,
                status="declined",
                error=f"permission denied for {resource}:{action}",
            )

        # Argument validation.
        validation_error = _validate_args(tool.args_schema, arguments)
        if validation_error:
            return await _record_terminal(
                session,
                conversation,
                message_id,
                tool,
                arguments,
                user_id,
                status="failed",
                error=f"invalid arguments: {validation_error}",
            )

        # Consequence gate (G1): spend AND mutating actions do NOT execute on the model's say-so.
        # Both create a plan and wait for explicit confirmation (owner rule: confirm all mutating).
        # An existing `plan` is appended to so the loop can batch several consequential steps from
        # one turn into a SINGLE confirmable plan (L3); with no plan a fresh one-step plan is made.
        if tool.consequence_class in ("spend", "mutating"):
            invocation = _new_invocation(
                conversation,
                message_id,
                tool,
                arguments,
                status="awaiting_confirmation",
                requires_confirmation=True,
            )
            session.add(invocation)
            await session.flush()
            # consequence_class travels with the step so the confirm UI can warn before a spend action
            # (e.g. launch_run) without re-deriving cost from the tool name.
            step = {"tool": tool.name, "args": arguments, "consequence_class": tool.consequence_class}
            if plan is None:
                plan = AssistantActionPlan(
                    conversation_id=conversation.id,
                    steps_json=[step],
                    status="proposed",
                )
                session.add(plan)
            else:
                # Reassign (not append in place) so SQLAlchemy flags the JSONB column dirty.
                plan.steps_json = [*(plan.steps_json or []), step]
            await session.flush()
            await _audit(session, user_id, invocation, tool, conversation, outcome="awaiting_confirmation")
            await session.commit()
            return ToolExecutionResult(status="awaiting_confirmation", tool_invocation=invocation, action_plan=plan)

        # Read / mutating: execute the handler in the user's context. A few tools report on the
        # conversation itself (e.g. list_session_activity) and opt into receiving it via the descriptor.
        handler_kwargs: dict[str, Any] = {"org_id": org_id, "user_id": user_id, "arguments": arguments}
        if tool.needs_conversation:
            handler_kwargs["conversation"] = conversation
        try:
            output = await tool.handler(session, **handler_kwargs)
        except Exception as exc:  # surface to the loop, never crash it
            return await _record_terminal(
                session,
                conversation,
                message_id,
                tool,
                arguments,
                user_id,
                status="failed",
                error=str(exc),
            )

        invocation = _new_invocation(conversation, message_id, tool, arguments, status="succeeded")
        invocation.result_json = output
        session.add(invocation)
        await session.flush()
        await _audit(session, user_id, invocation, tool, conversation, outcome="succeeded")
        await session.commit()
        return ToolExecutionResult(status="succeeded", tool_invocation=invocation, result=output)


async def _record_terminal(
    session: AsyncSession,
    conversation: AssistantConversation,
    message_id: int | None,
    tool: ToolDescriptor,
    arguments: dict,
    user_id: int,
    *,
    status: str,
    error: str,
) -> ToolExecutionResult:
    """Persist a rejected/failed invocation, audit the attempt, and return it to the loop."""
    invocation = _new_invocation(conversation, message_id, tool, arguments, status=status, error=error)
    session.add(invocation)
    await session.flush()
    await _audit(session, user_id, invocation, tool, conversation, outcome=status)
    await session.commit()
    return ToolExecutionResult(status=status, tool_invocation=invocation, error=error)
