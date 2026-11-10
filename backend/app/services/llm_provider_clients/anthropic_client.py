"""Anthropic Claude provider client."""

from __future__ import annotations

import logging

import httpx

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_clients.http_contract import (
    allowlisted_code,
    incomplete,
    json_object,
    object_list,
    parse_error,
    raise_for_status,
    send,
    transport_error,
)
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.anthropic")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://api.anthropic.com/v1"
# 300s read budget covers large prompts on Claude. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_API_VERSION = "2023-06-01"


_PROVIDER = "anthropic"
_LABEL = "Anthropic"

# Anthropic error types worth logging (error.type). Anything else is dropped.
_KNOWN_CODES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "not_found_error",
        "request_too_large",
        "rate_limit_error",
        "api_error",
        "overloaded_error",
    }
)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "Content-Type": "application/json",
    }


def _raise_for_status(resp: httpx.Response, operation: str) -> None:
    raise_for_status(
        resp, provider=_PROVIDER, operation=operation, upstream_code=allowlisted_code(resp, ("type",), _KNOWN_CODES)
    )


def _require_key(api_key: str | None) -> str:
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth", provider=_PROVIDER)
    return api_key


async def list_models(api_key: str | None) -> list[str]:
    key = _require_key(api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "GET",
                provider=_PROVIDER,
                operation="list_models",
                url=f"{_BASE_URL}/models",
                headers=_headers(key),
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="list_models") from exc
    _raise_for_status(resp, "list_models")
    data = json_object(resp, provider=_PROVIDER, label=_LABEL, what="/models")
    items = object_list(data.get("data"), provider=_PROVIDER, label=_LABEL, what="/models data")
    ids = [item.get("id") for item in items]
    if not all(isinstance(model_id, str) and model_id for model_id in ids):
        raise parse_error("Anthropic /models entry has no model id", provider=_PROVIDER)
    return ids


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    key = _require_key(api_key)
    body = {
        "model": model,
        "max_tokens": 4096,
        "system": prompt,
        "messages": [{"role": "user", "content": payload}],
    }
    logger.info(
        "anthropic submit: model=%s prompt_chars=%d payload_chars=%d",
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
                url=f"{_BASE_URL}/messages",
                headers=_headers(key),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit") from exc
    logger.info("anthropic submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    _raise_for_status(resp, "submit")
    text, _ = _parse_message(json_object(resp, provider=_PROVIDER, label=_LABEL, what="message"))
    if text is None:
        raise parse_error("Anthropic message has no text", provider=_PROVIDER)
    return text


def _parse_message(data: dict) -> tuple[str | None, list[ToolCall]]:
    """Text and tool calls from a Messages API response. A max_tokens stop is
    truncated output, never an answer; a refusal with no text has nothing to show."""
    stop_reason = data.get("stop_reason")
    if stop_reason == "max_tokens":
        raise incomplete("Anthropic response was truncated", provider=_PROVIDER, reason="max_tokens")
    parts = object_list(data.get("content"), provider=_PROVIDER, label=_LABEL, what="message content")
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        kind = part.get("type")
        if kind == "text":
            value = part.get("text")
            if not isinstance(value, str):
                raise parse_error("Anthropic text block is not text", provider=_PROVIDER)
            texts.append(value)
        elif kind == "tool_use":
            name, call_id, args = part.get("name"), part.get("id"), part.get("input")
            if not (isinstance(name, str) and name and isinstance(call_id, str) and call_id):
                raise parse_error("Anthropic tool call is missing its name or id", provider=_PROVIDER)
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise parse_error("Anthropic tool call input is not an object", provider=_PROVIDER)
            tool_calls.append(ToolCall(tool=name, args=args, id=call_id))
    text = "".join(texts) or None
    if text is None and stop_reason == "refusal":
        raise incomplete("Anthropic declined to respond", provider=_PROVIDER, reason="refusal")
    return text, tool_calls


def _tools_to_anthropic(tools: list[dict]) -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": object_schema(t["args_schema"])}
        for t in tools
    ]


def _messages_to_anthropic(messages: list[dict]) -> list[dict]:
    """Translate the loop's provider-agnostic messages into native Anthropic messages.

    Prior tool exchanges are threaded as native blocks: an assistant turn that called tools
    becomes a content list with `tool_use` blocks (each given a stable id), and the tool results
    that follow it become a single `user` message of `tool_result` blocks referencing those ids.
    Batching the results into one user message keeps the required user/assistant alternation.

    The loop persists results in call order immediately after the assistant message, so results
    are paired positionally. If a `tool_use` has no following result (a spend stop halts the loop
    for confirmation, leaving the call unanswered), a placeholder `tool_result` is synthesized so
    the request stays valid: Anthropic rejects any `tool_use` that is not answered.
    """
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
        elif role == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            call_ids: list[str] = []
            for call in m.get("tool_calls") or []:
                counter += 1
                tool_use_id = f"toolu_hist_{counter}"
                blocks.append(
                    {"type": "tool_use", "id": tool_use_id, "name": call["tool"], "input": call.get("args") or {}}
                )
                call_ids.append(tool_use_id)
            if not blocks:
                blocks.append({"type": "text", "text": "(no content)"})
            out.append({"role": "assistant", "content": blocks})
            i += 1
            # Pair each tool_use with the following tool result message, in order.
            if call_ids:
                result_blocks: list[dict] = []
                for tool_use_id in call_ids:
                    if i < n and messages[i].get("role") == "tool":
                        content = messages[i].get("content") or ""
                        i += 1
                    else:
                        content = "(awaiting confirmation; not yet executed)"
                    result_blocks.append({"type": "tool_result", "tool_use_id": tool_use_id, "content": content})
                out.append({"role": "user", "content": result_blocks})
        elif role == "tool":
            # A tool result with no preceding assistant tool_use (should not happen given loop
            # ordering). Fall back to plain text so the request stays well-formed.
            out.append({"role": "user", "content": m.get("content") or "(no content)"})
            i += 1
        else:
            i += 1
    return out


def _parse_anthropic_tool_use(data: dict) -> ToolUseResult:
    text, tool_calls = _parse_message(data)
    if text is None and not tool_calls:
        raise parse_error("Anthropic response has no text or tool calls", provider=_PROVIDER)
    return ToolUseResult(text=text, tool_calls=tool_calls)


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None, system: str | None = None
) -> ToolUseResult:
    key = _require_key(api_key)
    body: dict = {
        "model": model,
        "max_tokens": 4096,
        "tools": _tools_to_anthropic(tools),
        "messages": _messages_to_anthropic(messages),
    }
    if system:
        body["system"] = system
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "POST",
                provider=_PROVIDER,
                operation="submit_with_tools",
                url=f"{_BASE_URL}/messages",
                headers=_headers(key),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit_with_tools") from exc
    _raise_for_status(resp, "submit_with_tools")
    return _parse_anthropic_tool_use(json_object(resp, provider=_PROVIDER, label=_LABEL, what="tool-use"))
