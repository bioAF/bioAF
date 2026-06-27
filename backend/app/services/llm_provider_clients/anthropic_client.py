"""Anthropic Claude provider client."""

from __future__ import annotations

import logging

import httpx

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.anthropic")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://api.anthropic.com/v1"
# 300s read budget covers large prompts on Claude. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_API_VERSION = "2023-06-01"


def _transport_detail(exc: httpx.HTTPError) -> str:
    text = str(exc).strip()
    return text or repr(exc)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "Content-Type": "application/json",
    }


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise ProviderError(resp.text, error_class="auth")
    if resp.status_code == 429:
        raise ProviderError(resp.text, error_class="rate_limit")
    if resp.status_code >= 500:
        raise ProviderError(resp.text, error_class="server")
    if resp.status_code >= 400:
        raise ProviderError(resp.text, error_class="other")


async def list_models(api_key: str | None) -> list[str]:
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_BASE_URL}/models", headers=_headers(api_key))
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("anthropic list_models transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    _raise_for_status(resp)
    try:
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"Anthropic /models response not parseable: {exc}", error_class="parse") from exc


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth")
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
            resp = await client.post(
                f"{_BASE_URL}/messages",
                headers=_headers(api_key),
                json=body,
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("anthropic submit transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    logger.info("anthropic submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    if resp.status_code >= 400:
        logger.warning("anthropic submit non-2xx body: %s", resp.text[:2000])
    _raise_for_status(resp)
    try:
        data = resp.json()
        parts = data.get("content", [])
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"Anthropic message response not parseable: {exc}", error_class="parse") from exc


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
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text") or None
    tool_calls = [
        ToolCall(tool=p["name"], args=p.get("input", {}) or {}, id=p.get("id"))
        for p in parts
        if p.get("type") == "tool_use"
    ]
    return ToolUseResult(text=text, tool_calls=tool_calls)


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None, system: str | None = None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth")
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
            resp = await client.post(f"{_BASE_URL}/messages", headers=_headers(api_key), json=body)
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("anthropic submit_with_tools transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    _raise_for_status(resp)
    try:
        return _parse_anthropic_tool_use(resp.json())
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"Anthropic tool-use response not parseable: {exc}", error_class="parse") from exc
