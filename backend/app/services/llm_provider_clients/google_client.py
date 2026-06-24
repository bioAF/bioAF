"""Google Gemini provider client."""

from __future__ import annotations

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


def _messages_to_google(messages: list[dict]) -> list[dict]:
    """Translate the loop's messages into Gemini contents. Prior tool exchanges are encoded as
    plain text rather than native functionCall/functionResponse parts; the model's NEW decision
    still returns a native functionCall part we parse. v1 best-effort (see LEARNINGS)."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "user", "parts": [{"text": f"[tool result] {m.get('content') or ''}"}]})
            continue
        text = m.get("content") or ""
        if role == "assistant" and m.get("tool_calls"):
            calls = ", ".join(f"{c['tool']}({c['args']})" for c in m["tool_calls"])
            text = f"{text}\n[called tools: {calls}]".strip()
        out.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": text or "(no content)"}]})
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
