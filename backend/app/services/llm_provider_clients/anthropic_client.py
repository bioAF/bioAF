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
    """Translate the loop's provider-agnostic messages into Anthropic messages. Prior tool
    exchanges are encoded as plain text (not native tool_use/tool_result blocks) to sidestep
    tool_use_id and role-alternation constraints; the model's NEW decision still comes back as
    a native tool_use block, which we parse. v1 best-effort (see LEARNINGS)."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "user", "content": f"[tool result] {m.get('content') or ''}"})
            continue
        text = m.get("content") or ""
        if role == "assistant" and m.get("tool_calls"):
            calls = ", ".join(f"{c['tool']}({c['args']})" for c in m["tool_calls"])
            text = f"{text}\n[called tools: {calls}]".strip()
        out.append({"role": "assistant" if role == "assistant" else "user", "content": text or "(no content)"})
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
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth")
    body = {
        "model": model,
        "max_tokens": 4096,
        "tools": _tools_to_anthropic(tools),
        "messages": _messages_to_anthropic(messages),
    }
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
