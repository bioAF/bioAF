"""Google Gemini provider client."""

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
    parse_error,
    raise_for_status,
    send,
    transport_error,
)
from app.services.llm_provider_clients.tool_use import ToolCall, ToolUseResult, object_schema

logger = logging.getLogger("bioaf.llm.google")

# Native tool/function calling is available on this provider (assistant, L4).
SUPPORTS_TOOLS = True

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# 300s read budget covers large prompts on Gemini. The default httpx 60s
# routinely timed out experiment-scope reviews. Connect stays at 10s.
_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


_PROVIDER = "google"
_LABEL = "Google"

# Google RPC status names worth logging (error.status). Anything else is dropped.
_KNOWN_CODES = frozenset(
    {
        "INVALID_ARGUMENT",
        "FAILED_PRECONDITION",
        "OUT_OF_RANGE",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "RESOURCE_EXHAUSTED",
        "CANCELLED",
        "INTERNAL",
        "UNAVAILABLE",
        "DEADLINE_EXCEEDED",
    }
)
# finishReason values that end a candidate before a usable answer. Each is an
# allowlisted identifier carried as upstream_code.
_TRUNCATED = frozenset({"MAX_TOKENS"})
_FILTERED = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "IMAGE_SAFETY", "OTHER"}
)
_BLOCK_REASONS = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY", "OTHER"})


def _raise_for_status(resp: httpx.Response, operation: str) -> None:
    raise_for_status(
        resp, provider=_PROVIDER, operation=operation, upstream_code=allowlisted_code(resp, ("status",), _KNOWN_CODES)
    )


def _require_key(api_key: str | None) -> str:
    if not api_key:
        raise ProviderError("Google requires an API key", error_class="auth", provider=_PROVIDER)
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
                params={"key": key},
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="list_models") from exc
    _raise_for_status(resp, "list_models")
    data = json_object(resp, provider=_PROVIDER, label=_LABEL, what="/models")
    items = object_list(data.get("models"), provider=_PROVIDER, label=_LABEL, what="/models list")
    models = []
    for item in items:
        name = item.get("name")
        if not isinstance(name, str):
            raise parse_error("Google /models entry has no model name", provider=_PROVIDER)
        if name.startswith("models/"):
            name = name[len("models/") :]
        if name:
            models.append(name)
    return models


async def submit(
    prompt: str,
    payload: str,
    model: str,
    api_key: str | None,
    attachments: list[dict] | None = None,
) -> str:
    key = _require_key(api_key)
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
            resp = await send(
                client,
                "POST",
                provider=_PROVIDER,
                operation="submit",
                url=f"{_BASE_URL}/models/{model}:generateContent",
                params={"key": key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit") from exc
    logger.info("google submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    _raise_for_status(resp, "submit")
    text, _ = _parse_candidate(json_object(resp, provider=_PROVIDER, label=_LABEL, what="generateContent"))
    if text is None:
        raise parse_error("Google generateContent response has no text", provider=_PROVIDER)
    return text


def _parse_candidate(data: dict) -> tuple[str | None, list[ToolCall]]:
    """Text and function calls from the first candidate. A blocked prompt, a
    truncated candidate, or a filtered candidate is never an answer."""
    feedback = data.get("promptFeedback")
    block = feedback.get("blockReason") if isinstance(feedback, dict) else None
    candidates = data.get("candidates")
    if block and not candidates:
        reason = block if block in _BLOCK_REASONS else None
        raise incomplete("Google blocked the prompt", provider=_PROVIDER, reason=reason)
    candidates = object_list(candidates, provider=_PROVIDER, label=_LABEL, what="candidates")
    if not candidates:
        raise parse_error("Google response has no candidates", provider=_PROVIDER)
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish in _TRUNCATED:
        raise incomplete("Google response was truncated", provider=_PROVIDER, reason=finish)
    if finish in _FILTERED:
        raise incomplete("Google response was filtered", provider=_PROVIDER, reason=finish)
    content = candidate.get("content")
    if not isinstance(content, dict):
        raise parse_error("Google candidate has no content", provider=_PROVIDER)
    parts = object_list(content.get("parts"), provider=_PROVIDER, label=_LABEL, what="candidate parts")
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if "text" in part:
            value = part["text"]
            if not isinstance(value, str):
                raise parse_error("Google text part is not text", provider=_PROVIDER)
            texts.append(value)
        if "functionCall" in part:
            call = part["functionCall"]
            if not isinstance(call, dict):
                raise parse_error("Google function call is not an object", provider=_PROVIDER)
            name, args = call.get("name"), call.get("args")
            if not (isinstance(name, str) and name):
                raise parse_error("Google function call is missing its name", provider=_PROVIDER)
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise parse_error("Google function call args are not an object", provider=_PROVIDER)
            tool_calls.append(ToolCall(tool=name, args=args))
    return "".join(texts) or None, tool_calls


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
    text, tool_calls = _parse_candidate(data)
    if text is None and not tool_calls:
        raise parse_error("Google response has no text or function calls", provider=_PROVIDER)
    return ToolUseResult(text=text, tool_calls=tool_calls)


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None, system: str | None = None
) -> ToolUseResult:
    key = _require_key(api_key)
    body: dict = {
        "contents": _messages_to_google(messages),
        "tools": _tools_to_google(tools),
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await send(
                client,
                "POST",
                provider=_PROVIDER,
                operation="submit_with_tools",
                url=f"{_BASE_URL}/models/{model}:generateContent",
                params={"key": key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise transport_error(exc, provider=_PROVIDER, label=_LABEL, operation="submit_with_tools") from exc
    _raise_for_status(resp, "submit_with_tools")
    return _parse_google_tool_use(json_object(resp, provider=_PROVIDER, label=_LABEL, what="tool-use"))
