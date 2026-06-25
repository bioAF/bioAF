"""OpenAI provider client.

Uses the public REST API directly via httpx so we are not coupled to the
upstream SDK's version dance. We hit two endpoints:

    GET  https://api.openai.com/v1/models
    POST https://api.openai.com/v1/chat/completions
"""

from __future__ import annotations

import json
import logging

import httpx

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.openai")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://api.openai.com/v1"
# 300s read budget covers large prompts on GPT. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def _transport_detail(exc: httpx.HTTPError) -> str:
    text = str(exc).strip()
    return text or repr(exc)


async def list_models(api_key: str | None) -> list[str]:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE_URL}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("openai list_models transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc

    if resp.status_code in (401, 403):
        raise ProviderError(resp.text, error_class="auth")
    if resp.status_code == 429:
        raise ProviderError(resp.text, error_class="rate_limit")
    if resp.status_code >= 500:
        raise ProviderError(resp.text, error_class="server")
    if resp.status_code >= 400:
        raise ProviderError(resp.text, error_class="other")

    try:
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"OpenAI /models response not parseable: {exc}", error_class="parse") from exc


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth")
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
            resp = await client.post(
                f"{_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("openai submit transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc

    logger.info("openai submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    if resp.status_code >= 400:
        logger.warning("openai submit non-2xx body: %s", resp.text[:2000])

    if resp.status_code in (401, 403):
        raise ProviderError(resp.text, error_class="auth")
    if resp.status_code == 429:
        raise ProviderError(resp.text, error_class="rate_limit")
    if resp.status_code >= 500:
        raise ProviderError(resp.text, error_class="server")
    if resp.status_code >= 400:
        raise ProviderError(resp.text, error_class="other")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"OpenAI completion response not parseable: {exc}", error_class="parse") from exc


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise ProviderError(resp.text, error_class="auth")
    if resp.status_code == 429:
        raise ProviderError(resp.text, error_class="rate_limit")
    if resp.status_code >= 500:
        raise ProviderError(resp.text, error_class="server")
    if resp.status_code >= 400:
        raise ProviderError(resp.text, error_class="other")


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": object_schema(t["args_schema"]),
            },
        }
        for t in tools
    ]


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """Translate the loop's messages into native OpenAI chat messages.

    An assistant turn that called tools becomes an assistant message with a `tool_calls` array
    (each call given a stable id and JSON-string arguments); the results that follow become
    `tool`-role messages carrying the matching `tool_call_id`. OpenAI does not require role
    alternation for tool messages, so each result is its own message. Every assistant tool_call
    must be answered, so an unanswered call (a spend stop halts the loop) gets a synthesized
    placeholder tool message. Results are paired to calls positionally (the loop persists them in
    call order right after the assistant message)."""
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
            tool_calls = m.get("tool_calls") or []
            if not tool_calls:
                out.append({"role": "assistant", "content": m.get("content") or "(no content)"})
                i += 1
                continue
            call_ids: list[str] = []
            native_calls: list[dict] = []
            for call in tool_calls:
                counter += 1
                call_id = f"call_hist_{counter}"
                call_ids.append(call_id)
                native_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": call["tool"], "arguments": json.dumps(call.get("args") or {})},
                    }
                )
            out.append({"role": "assistant", "content": m.get("content"), "tool_calls": native_calls})
            i += 1
            for call_id in call_ids:
                if i < n and messages[i].get("role") == "tool":
                    content = messages[i].get("content") or ""
                    i += 1
                else:
                    content = "(awaiting confirmation; not yet executed)"
                out.append({"role": "tool", "tool_call_id": call_id, "content": content})
        elif role == "tool":
            # Orphan tool result with no preceding tool_call (should not happen). Plain-text fallback.
            out.append({"role": "user", "content": m.get("content") or "(no content)"})
            i += 1
        else:
            i += 1
    return out


def _parse_openai_tool_use(data: dict) -> ToolUseResult:
    message = data["choices"][0]["message"]
    text = message.get("content") or None
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(ToolCall(tool=function.get("name"), args=args, id=call.get("id")))
    return ToolUseResult(text=text, tool_calls=tool_calls)


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth")
    body = {
        "model": model,
        "messages": _messages_to_openai(messages),
        "tools": _tools_to_openai(tools),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("openai submit_with_tools transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    _raise_for_status(resp)
    try:
        return _parse_openai_tool_use(resp.json())
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"OpenAI tool-use response not parseable: {exc}", error_class="parse") from exc
