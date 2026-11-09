"""Assistant conversation history: internal link encoding and its two projections (plan 12.5).

Stored assistant messages keep the public call shape ``{"tool", "args"}`` in ``tool_calls_json``.
Internal data rides alongside under namespaced, versioned keys that only this module reads:

- Each call entry may carry ``"_bioaf": {"v": 1, "call_id", "invocation_id", "result_message_id"}``:
  the provider's function-call id, the AssistantToolInvocation recorded for the call, and the
  tool-role message holding its result.
- An assistant message may end with one turn-state entry
  ``{"_bioaf_turn": {"v": 1, "provider", "model", "items": [...]}}``: the provider's ordered output
  items (opaque reasoning/continuation state) for stateless replay.
- A plan step may carry ``"_bioaf": {"v": 1, "message_id", "call_index", "invocation_id"}``.

``public_*`` strip all of it for API responses. ``build_provider_history`` produces the list the
provider adapters consume: each assistant tool-call entry is followed by exactly one tool entry per
call, in call order, matched by link. Messages stored before call tracking ("legacy") are paired in
order only when every call has a result row; otherwise no result is attributed to any call.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import AssistantMessage, AssistantToolInvocation
from app.services.assistant_untrusted import fence_tool_result
from app.services.llm_provider_clients.tool_use import ToolCall

LINK_KEY = "_bioaf"
TURN_KEY = "_bioaf_turn"
VERSION = 1

_AWAITING = json.dumps(
    {
        "status": "awaiting_confirmation",
        "note": "Proposed in an action plan for the user's confirmation. It has not run.",
    }
)
_NOT_RECORDED = json.dumps(
    {
        "status": "unknown",
        "note": (
            "The outcome of this call was not recorded in this conversation's history. Do not assume "
            "whether it ran; check the current state with a read tool if it matters."
        ),
    }
)


def call_entry(call: ToolCall) -> dict:
    return {"tool": call.tool, "args": call.args, LINK_KEY: {"v": VERSION, "call_id": call.id}}


def turn_state_entry(state: dict) -> dict:
    return {TURN_KEY: {"v": VERSION, **state}}


def calls_of(tool_calls_json: list | None) -> list[dict]:
    return [e for e in (tool_calls_json or []) if isinstance(e, dict) and "tool" in e]


def public_tool_calls(tool_calls_json: list | None) -> list[dict] | None:
    calls = [{"tool": e["tool"], "args": e.get("args")} for e in calls_of(tool_calls_json)]
    return calls or None


def public_plan_steps(steps: list | None) -> list[dict] | None:
    if steps is None:
        return None
    return [{k: v for k, v in step.items() if k != LINK_KEY} for step in steps]


def link_call(tool_calls_json: list, index: int, **links: Any) -> list:
    """Return a copy of ``tool_calls_json`` with ``links`` merged into call ``index``'s link block.
    A new list, so SQLAlchemy sees the JSONB column change."""
    entries = [dict(e) for e in tool_calls_json]
    positions = [i for i, e in enumerate(entries) if "tool" in e]
    pos = positions[index]
    entries[pos][LINK_KEY] = {**entries[pos].get(LINK_KEY, {"v": VERSION}), **links}
    return entries


def _turn_state(tool_calls_json: list | None) -> dict | None:
    for e in tool_calls_json or []:
        if isinstance(e, dict) and TURN_KEY in e:
            return e[TURN_KEY]
    return None


def _placeholder(invocation: AssistantToolInvocation | None) -> str:
    if invocation is not None and invocation.status == "awaiting_confirmation":
        return _AWAITING
    return _NOT_RECORDED


def _entry(role: str, content: str | None, **extra: Any) -> dict:
    return {"role": role, "content": content, "tool_calls": None, "tool_invocation_id": None, **extra}


async def build_provider_history(
    session: AsyncSession, conversation_id: int, *, provider: str | None = None, model: str | None = None
) -> list[dict]:
    """The conversation as the provider adapters consume it.

    Tool results are untrusted input (spec-03): fenced and neutralized here, at the model boundary
    only; stored rows stay raw. Provider turn state is attached only when it was recorded by the
    same provider and model now in use, so it is never replayed to a different provider or model."""
    rows = list(
        (
            await session.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.id)
            )
        ).scalars()
    )
    invocations = {
        inv.id: inv
        for inv in (
            await session.execute(
                select(AssistantToolInvocation).where(AssistantToolInvocation.conversation_id == conversation_id)
            )
        ).scalars()
    }
    by_id = {m.id: m for m in rows}
    consumed: set[int] = set()
    for m in rows:
        for e in calls_of(m.tool_calls_json):
            result_id = (e.get(LINK_KEY) or {}).get("result_message_id")
            if result_id is not None:
                consumed.add(result_id)

    out: list[dict] = []
    for idx, m in enumerate(rows):
        if m.id in consumed:
            continue
        if m.role == "user":
            out.append(_entry("user", m.content))
            continue
        if m.role == "tool":
            # Unattached result (no preceding call). Adapters render it as plain context.
            out.append(_entry("tool", fence_tool_result(m.content or ""), tool_invocation_id=m.tool_invocation_id))
            continue
        if m.role != "assistant":
            continue

        calls = calls_of(m.tool_calls_json)
        assistant = _entry("assistant", m.content)
        state = _turn_state(m.tool_calls_json)
        if state and provider is not None and state.get("provider") == provider and state.get("model") == model:
            assistant["provider_state"] = state
        if not calls:
            out.append(assistant)
            continue

        results: list[dict] = []
        if all(LINK_KEY in e for e in calls):
            normalized = []
            for i, e in enumerate(calls):
                link = e[LINK_KEY]
                call_id = link.get("call_id") or f"bioaf_{m.id}_{i}"
                normalized.append({"tool": e["tool"], "args": e.get("args") or {}, "call_id": call_id})
                result_row = by_id.get(link.get("result_message_id")) if link.get("result_message_id") else None
                if result_row is not None:
                    content = fence_tool_result(result_row.content or "")
                    inv_id = result_row.tool_invocation_id
                else:
                    content = _placeholder(invocations.get(link.get("invocation_id")))
                    inv_id = link.get("invocation_id")
                results.append(_entry("tool", content, tool_invocation_id=inv_id, tool_call_id=call_id))
        else:
            # Legacy: results were written in call order right after the message, but a pending
            # call wrote none, so only a complete set can be attributed.
            following: list[AssistantMessage] = []
            for later in rows[idx + 1 :]:
                if later.role != "tool" or later.id in consumed:
                    break
                following.append(later)
            normalized = [
                {"tool": e["tool"], "args": e.get("args") or {}, "call_id": f"bioaf_legacy_{m.id}_{i}"}
                for i, e in enumerate(calls)
            ]
            if len(following) == len(calls):
                for call, row in zip(normalized, following):
                    results.append(
                        _entry(
                            "tool",
                            fence_tool_result(row.content or ""),
                            tool_invocation_id=row.tool_invocation_id,
                            tool_call_id=call["call_id"],
                        )
                    )
            else:
                for call in normalized:
                    results.append(_entry("tool", _NOT_RECORDED, tool_call_id=call["call_id"]))
                if following:
                    orphans = "\n".join(fence_tool_result(r.content or "") for r in following)
                    note = f"[Earlier tool results that cannot be matched to specific calls]\n{orphans}"
                    assistant["content"] = f"{m.content}\n\n{note}" if m.content else note
            consumed.update(r.id for r in following)

        assistant["tool_calls"] = normalized
        out.append(assistant)
        out.extend(results)
    return out
