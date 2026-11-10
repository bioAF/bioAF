"""OpenAI provider client.

Uses the public REST API directly via httpx so we are not coupled to the
upstream SDK's version dance. We hit three endpoints:

    GET  https://api.openai.com/v1/models
    POST https://api.openai.com/v1/chat/completions   (non-tool review submissions)
    POST https://api.openai.com/v1/responses          (assistant tool calling)

Tool calling uses the Responses API because current models (for example gpt-6.1-sol) support
function tools only there.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_clients.http_contract import (
    allowlisted_code,
    incomplete,
    json_object,
    object_list,
    optional_text,
    parse_error,
    raise_for_status,
    send,
    transport_error,
)
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.openai")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://api.openai.com/v1"
# 300s read budget covers large prompts on GPT. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


_PROVIDER = "openai"
_LABEL = "OpenAI"


async def list_models(api_key: str | None) -> list[str]:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth", provider=_PROVIDER)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "GET",
                provider=_PROVIDER,
                operation="list_models",
                url=f"{_BASE_URL}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="list_models") from exc
    raise_for_status(resp, provider=_PROVIDER, operation="list_models", upstream_code=_upstream_code(resp))
    data = json_object(resp, provider=_PROVIDER, label=_LABEL, what="/models")
    items = object_list(data.get("data"), provider=_PROVIDER, label=_LABEL, what="/models data")
    ids = [item.get("id") for item in items]
    if not all(isinstance(model_id, str) and model_id for model_id in ids):
        raise parse_error("OpenAI /models entry has no model id", provider=_PROVIDER)
    return ids


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth", provider=_PROVIDER)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": payload},
        ],
    }
    logger.info(
        "openai submit: model=%s prompt_chars=%d payload_chars=%d",
        model,
        len(prompt),
        len(payload),
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "POST",
                provider=_PROVIDER,
                operation="submit",
                url=f"{_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit") from exc

    logger.info("openai submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    raise_for_status(resp, provider=_PROVIDER, operation="submit", upstream_code=_upstream_code(resp))
    return _parse_chat_completion(json_object(resp, provider=_PROVIDER, label=_LABEL, what="completion"))


# finish_reason values that end a completion before a usable answer.
_INCOMPLETE_FINISH = {"length": "OpenAI completion was truncated", "content_filter": "OpenAI completion was filtered"}


def _parse_chat_completion(data: dict) -> str:
    choices = object_list(data.get("choices"), provider=_PROVIDER, label=_LABEL, what="completion choices")
    if not choices:
        raise parse_error("OpenAI completion has no choices", provider=_PROVIDER)
    choice = choices[0]
    finish = optional_text(choice.get("finish_reason"), provider=_PROVIDER, label=_LABEL, what="finish_reason")
    if finish in _INCOMPLETE_FINISH:
        raise incomplete(_INCOMPLETE_FINISH[finish], provider=_PROVIDER, reason=finish)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise parse_error("OpenAI completion choice has no message", provider=_PROVIDER)
    content = message.get("content")
    if isinstance(content, str) and content:
        return content
    refusal = message.get("refusal")
    if content is None and isinstance(refusal, str) and refusal:
        return refusal
    raise parse_error("OpenAI completion has no text", provider=_PROVIDER)


# Provider error codes/types worth logging. Only these exact values are carried: a value merely
# shaped like an identifier can still be a key or account id, so anything else is dropped.
_KNOWN_CODES = frozenset(
    {
        "invalid_api_key",
        "insufficient_quota",
        "rate_limit_exceeded",
        "model_not_found",
        "unsupported_parameter",
        "unsupported_value",
        "invalid_value",
        "missing_required_parameter",
        "context_length_exceeded",
        "request_timeout",
        "server_error",
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "not_found_error",
        "rate_limit_error",
        "insufficient_quota_error",
    }
)


def _upstream_code(resp: httpx.Response) -> str | None:
    """The provider's error code (or type), only when it is a recognized value."""
    return allowlisted_code(resp, ("code", "type"), _KNOWN_CODES)


def _tools_to_responses(tools: list[dict]) -> list[dict]:
    # strict=False keeps optional arguments optional; strict mode would require every property.
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": object_schema(t["args_schema"]),
            "strict": False,
        }
        for t in tools
    ]


def _messages_to_responses(messages: list[dict]) -> list[dict]:
    """Translate the loop's history into Responses input items.

    A turn recorded by this provider and model carries its original output items
    (``provider_state``): those are replayed verbatim and in order, which keeps encrypted reasoning
    and each message's ``phase``. Otherwise the turn is rebuilt from its text and normalized calls.
    Each call's result becomes a ``function_call_output`` matched by call id. A call with no result
    entry (only possible for histories not built by the assistant history module) is answered with
    a pending placeholder, since Responses requires every call to have an output."""
    out: list[dict] = []
    counter = 0
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content") or "(no content)"})
            i += 1
            continue
        if role == "tool":
            # Orphan tool result with no preceding call (should not happen). Plain-text fallback.
            out.append({"role": "user", "content": m.get("content") or "(no content)"})
            i += 1
            continue
        if role != "assistant":
            i += 1
            continue

        calls = m.get("tool_calls") or []
        state = m.get("provider_state")
        call_ids: list[str] = []
        for call in calls:
            counter += 1
            call_ids.append(call.get("call_id") or f"call_hist_{counter}")
        if state and isinstance(state.get("items"), list):
            out.extend(state["items"])
        else:
            if m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
            for call, call_id in zip(calls, call_ids):
                out.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": call["tool"],
                        "arguments": json.dumps(call.get("args") or {}),
                    }
                )
        i += 1
        for call_id in call_ids:
            if i < n and messages[i].get("role") == "tool":
                output = messages[i].get("content") or ""
                i += 1
            else:
                output = "(awaiting confirmation; not yet executed)"
            out.append({"type": "function_call_output", "call_id": call_id, "output": output})
    return out


def _parse_responses_tool_use(data: dict, model: str) -> ToolUseResult:
    status = optional_text(data.get("status"), provider=_PROVIDER, label=_LABEL, what="response status")
    if status == "incomplete":
        raise ProviderError("OpenAI response incomplete", error_class="incomplete", provider=_PROVIDER)
    if status == "failed":
        raise ProviderError("OpenAI response failed", error_class="server", provider=_PROVIDER)
    if status not in (None, "completed"):
        raise ProviderError("OpenAI response not completed", error_class="incomplete", provider=_PROVIDER)

    output = object_list(data.get("output"), provider=_PROVIDER, label=_LABEL, what="response output")
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            parts = object_list(
                item.get("content") or [], provider=_PROVIDER, label=_LABEL, what="response message content"
            )
            for part in parts:
                if part.get("type") == "output_text" and isinstance(part.get("text"), str) and part["text"]:
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str) and part["refusal"]:
                    texts.append(part["refusal"])
        elif kind == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except (ValueError, TypeError) as exc:
                raise parse_error("OpenAI function call arguments are not valid JSON", provider=_PROVIDER) from exc
            if not isinstance(args, dict):
                raise parse_error("OpenAI function call arguments are not an object", provider=_PROVIDER)
            name, call_id = item.get("name"), item.get("call_id")
            if not (isinstance(name, str) and name and isinstance(call_id, str) and call_id):
                raise parse_error("OpenAI function call is missing its name or call id", provider=_PROVIDER)
            tool_calls.append(ToolCall(tool=name, args=args, id=call_id))
    text = "\n\n".join(texts) or None
    if text is None and not tool_calls:
        raise parse_error("OpenAI response has no text or function calls", provider=_PROVIDER)
    return ToolUseResult(
        text=text,
        tool_calls=tool_calls,
        provider_state={"provider": "openai", "model": model, "items": output},
    )


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None, system: str | None = None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth", provider=_PROVIDER)
    # bioAF owns the conversation history (store=false) and replays it each request. No reasoning
    # settings are sent: the model's own default applies.
    body: dict = {
        "model": model,
        "input": _messages_to_responses(messages),
        "tools": _tools_to_responses(tools),
        "store": False,
    }
    if system:
        body["instructions"] = system
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "POST",
                provider=_PROVIDER,
                operation="submit_with_tools",
                url=f"{_BASE_URL}/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit_with_tools") from exc
    raise_for_status(
        resp,
        provider=_PROVIDER,
        operation="submit_with_tools",
        upstream_code=_upstream_code(resp),
        message=f"OpenAI responses request failed with HTTP {resp.status_code}",
    )
    return _parse_responses_tool_use(json_object(resp, provider=_PROVIDER, label=_LABEL, what="tool-use"), model)
