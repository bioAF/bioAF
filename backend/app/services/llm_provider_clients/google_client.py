"""Google Gemini provider client."""

from __future__ import annotations

import json
import logging

import httpx

from app.services.llm_provider_clients import ProviderError
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.google")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# 300s read budget covers large prompts on Gemini. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def _transport_detail(exc: httpx.HTTPError) -> str:
    text = str(exc).strip()
    return text or repr(exc)


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
        raise ProviderError("Google requires an API key", error_class="auth")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_BASE_URL}/models", params={"key": api_key})
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("google list_models transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    _raise_for_status(resp)
    try:
        data = resp.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/") :]
            if name:
                models.append(name)
        return models
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(f"Google /models response not parseable: {exc}", error_class="parse") from exc


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    if not api_key:
        raise ProviderError("Google requires an API key", error_class="auth")
    body = {
        "contents": [{"role": "user", "parts": [{"text": f"{prompt}\n\n{payload}"}]}],
    }
    logger.info(
        "google submit: model=%s prompt_chars=%d payload_chars=%d",
        model,
        len(prompt),
        len(payload),
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE_URL}/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("google submit transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    logger.info("google submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    if resp.status_code >= 400:
        logger.warning("google submit non-2xx body: %s", resp.text[:2000])
    _raise_for_status(resp)
    try:
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Google generateContent response not parseable: {exc}", error_class="parse") from exc


def _tools_to_google(tools: list[dict]) -> list[dict]:
    return [
        {
            "function_declarations": [
                {"name": t["name"], "description": t["description"], "parameters": object_schema(t["args_schema"])}
                for t in tools
            ]
        }
    ]


def _as_response_object(content: str) -> dict:
    """Gemini's functionResponse.response must be a JSON object. The loop's tool content is a JSON
    string; use it directly when it parses to an object, else wrap it."""
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    return parsed if isinstance(parsed, dict) else {"content": content}


def _messages_to_google(messages: list[dict]) -> list[dict]:
    """Translate the loop's messages into native Gemini contents.

    An assistant turn that called tools becomes a `model` content with `functionCall` parts; the
    results that follow become one `user` content of `functionResponse` parts (batched to keep the
    user/model alternation). Gemini matches a response to its call by function NAME, paired
    positionally with the calls (the loop persists results in call order). An unanswered call (a
    spend stop halts the loop) gets a synthesized placeholder response."""
    out: list[dict] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "parts": [{"text": m.get("content") or "(no content)"}]})
            i += 1
        elif role == "assistant":
            parts: list[dict] = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            names: list[str] = []
            for call in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": call["tool"], "args": call.get("args") or {}}})
                names.append(call["tool"])
            if not parts:
                parts.append({"text": "(no content)"})
            out.append({"role": "model", "parts": parts})
            i += 1
            if names:
                response_parts: list[dict] = []
                for name in names:
                    if i < n and messages[i].get("role") == "tool":
                        content = messages[i].get("content") or ""
                        i += 1
                    else:
                        content = '{"status": "awaiting_confirmation"}'
                    response_parts.append(
                        {"functionResponse": {"name": name, "response": _as_response_object(content)}}
                    )
                out.append({"role": "user", "parts": response_parts})
        elif role == "tool":
            # Orphan tool result with no preceding functionCall (should not happen). Text fallback.
            out.append({"role": "user", "parts": [{"text": m.get("content") or "(no content)"}]})
            i += 1
        else:
            i += 1
    return out


def _parse_google_tool_use(data: dict) -> ToolUseResult:
    parts = data["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if "text" in p) or None
    tool_calls = [
        ToolCall(tool=p["functionCall"]["name"], args=p["functionCall"].get("args", {}) or {})
        for p in parts
        if "functionCall" in p
    ]
    return ToolUseResult(text=text, tool_calls=tool_calls)


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("Google requires an API key", error_class="auth")
    body = {
        "contents": _messages_to_google(messages),
        "tools": _tools_to_google(tools),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE_URL}/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        detail = _transport_detail(exc)
        logger.warning("google submit_with_tools transport failure: %s", detail)
        raise ProviderError(detail, error_class="transport") from exc
    _raise_for_status(resp)
    try:
        return _parse_google_tool_use(resp.json())
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Google tool-use response not parseable: {exc}", error_class="parse") from exc
