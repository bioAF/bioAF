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
import re

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


_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _upstream_code(resp: httpx.Response) -> str | None:
    """The provider's machine-readable error code (or type), only when it is a short identifier."""
    try:
        error = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict):
        return None
    for key in ("code", "type"):
        value = error.get(key)
        if isinstance(value, str) and _SAFE_CODE.match(value):
            return value
    return None


def _tool_http_error(resp: httpx.Response) -> ProviderError:
    status = resp.status_code
    if status in (401, 403):
        error_class = "auth"
    elif status == 429:
        error_class = "rate_limit"
    elif status >= 500:
        error_class = "server"
    else:
        error_class = "other"
    return ProviderError(
        f"OpenAI responses request failed with HTTP {status}",
        error_class=error_class,
        upstream_status=status,
        upstream_code=_upstream_code(resp),
    )


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
    status = data.get("status")
    if status == "incomplete":
        raise ProviderError("OpenAI response incomplete", error_class="incomplete")
    if status == "failed":
        raise ProviderError("OpenAI response failed", error_class="server")
    if status not in (None, "completed"):
        raise ProviderError("OpenAI response not completed", error_class="incomplete")

    output = data["output"]
    if not isinstance(output, list):
        raise TypeError("output is not a list")
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and part.get("refusal"):
                    texts.append(part["refusal"])
        elif kind == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except (ValueError, TypeError) as exc:
                raise ProviderError("OpenAI function call arguments are not valid JSON", error_class="parse") from exc
            if not isinstance(args, dict):
                raise ProviderError("OpenAI function call arguments are not an object", error_class="parse")
            if not item.get("name") or not item.get("call_id"):
                raise ProviderError("OpenAI function call is missing its name or call id", error_class="parse")
            tool_calls.append(ToolCall(tool=item["name"], args=args, id=item["call_id"]))
    text = "\n\n".join(texts) or None
    if text is None and not tool_calls:
        raise ProviderError("OpenAI response has no text or function calls", error_class="parse")
    return ToolUseResult(
        text=text,
        tool_calls=tool_calls,
        provider_state={"provider": "openai", "model": model, "items": output},
    )


async def submit_with_tools(
    *, messages: list[dict], tools: list[dict], model: str, api_key: str | None, system: str | None = None
) -> ToolUseResult:
    if not api_key:
        raise ProviderError("OpenAI requires an API key", error_class="auth")
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
            resp = await client.post(
                f"{_BASE_URL}/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        logger.warning("openai submit_with_tools transport failure: exception_type=%s timeout=true", type(exc).__name__)
        raise ProviderError("OpenAI request timed out", error_class="transport", timeout=True) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "openai submit_with_tools transport failure: exception_type=%s timeout=false", type(exc).__name__
        )
        raise ProviderError(f"OpenAI transport failure: {type(exc).__name__}", error_class="transport") from exc
    if resp.status_code >= 400:
        raise _tool_http_error(resp)
    try:
        return _parse_responses_tool_use(resp.json(), model)
    except ProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProviderError("OpenAI tool-use response not parseable", error_class="parse") from exc
