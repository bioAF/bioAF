"""Anthropic Claude provider client."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from app.services.llm_provider_clients import ModelAnswer, ProviderError, as_int
from app.services.llm_provider_clients.transport import (
    MAX_ATTEMPTS,
    backoff_seconds,
    is_retryable,
    refusal,
    request_with_retry,
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

# What a call that names no budget sends, as it always has.
DEFAULT_MAX_TOKENS = 4096

# plan_8_1 section 1.1: a call that names its own budget is streamed. A 16,000-token answer from an
# Opus-class model takes minutes, and a request that sends nothing back until it has finished runs into
# the 300 s read timeout above, or an idle connection dropped along the way. A stream sends text and
# pings throughout, so the read timeout only ever measures a stall. This bounds the whole answer.
STREAM_DEADLINE_SECONDS = 900.0

# Mid-stream error events that are the provider's own transient failure, retried within the transport
# policy exactly as a 429 or a 5xx before the stream would be.
_RETRYABLE_STREAM_ERRORS = ("overloaded_error", "api_error", "rate_limit_error")


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
    max_tokens: int | None = None,
) -> ModelAnswer:
    """One answer. ``max_tokens`` omitted sends 4096, as always; given, it is sent and the call streams."""
    if not api_key:
        raise ProviderError("Anthropic requires an API key", error_class="auth")
    body: dict = {
        "model": model,
        "max_tokens": max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        "system": prompt,
        "messages": [{"role": "user", "content": payload}],
    }
    logger.info(
        "anthropic submit: model=%s prompt_chars=%d payload_chars=%d max_tokens=%d streamed=%s",
        model,
        len(prompt),
        len(payload),
        body["max_tokens"],
        max_tokens is not None,
    )
    if max_tokens is not None:
        body["stream"] = True
        text, output_tokens, input_tokens, stop_reason = await _streamed(body, api_key)
    else:
        text, output_tokens, input_tokens, stop_reason = await _unstreamed(body, api_key)
    logger.info(
        "anthropic submit usage: model=%s output_tokens=%s input_tokens=%s stop_reason=%s",
        model,
        output_tokens,
        input_tokens,
        stop_reason or "unknown",
    )
    return _answer(text, output_tokens=output_tokens, input_tokens=input_tokens, stop_reason=stop_reason)


def _answer(text: str, *, output_tokens: int | None, input_tokens: int | None, stop_reason: str | None) -> ModelAnswer:
    """The answer, or the typed failure its stop reason says it is."""
    stop = (stop_reason or "").lower()
    # A safety block comes back 200 with `stop_reason: "refusal"` and the refusal prose as an
    # ordinary text block. Nothing read the field, so the caller saw prose with no fenced JSON in it
    # and reported an unparseable answer, which is a true statement about the text and a false one
    # about what happened.
    if stop == "refusal":
        raise refusal(text or "the model declined to answer")
    # change_7.2 section 7: a capped answer is not an unparseable one. `max_tokens` returns a
    # well-formed prefix that stops mid-JSON, and reporting it as "the model's answer was not in the
    # format bioAF asked for" is a true statement about the text and a false one about what happened.
    # That is the same misreading the refusal handling above was written to eliminate.
    # plan_8_1 section 1.2: the prefix travels with the failure, for diagnosis only.
    if stop == "max_tokens":
        raise ProviderError(
            "the model's answer was cut off at the token limit before it finished",
            error_class="truncated",
            text=text,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
        )
    # No text at all is the same event by a different route. An empty string would be reported as
    # unparseable for the same wrong reason.
    if not text:
        raise refusal("the model returned no content")
    return ModelAnswer(text, output_tokens=output_tokens, input_tokens=input_tokens, stop_reason=stop_reason)


async def _unstreamed(body: dict, api_key: str) -> tuple[str, int | None, int | None, str | None]:
    async def _send():
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            return await client.post(f"{_BASE_URL}/messages", headers=_headers(api_key), json=body)

    resp = await request_with_retry(_send, what="anthropic submit")
    logger.info("anthropic submit response: status=%d bytes=%d", resp.status_code, len(resp.content))
    if resp.status_code >= 400:
        logger.warning("anthropic submit non-2xx body: %s", resp.text[:2000])
    _raise_for_status(resp)
    try:
        data = resp.json()
        parts = data.get("content", [])
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        usage = data.get("usage") or {}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProviderError(f"Anthropic message response not parseable: {exc}", error_class="parse") from exc
    return text, as_int(usage.get("output_tokens")), as_int(usage.get("input_tokens")), data.get("stop_reason")


class _StreamState:
    """What one streamed attempt has received so far. Kept outside the attempt so a timeout can still
    report the text that arrived."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.output_tokens: int | None = None
        self.input_tokens: int | None = None
        self.stop_reason: str | None = None
        self.error: dict | None = None

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def read(self, data: dict) -> None:
        kind = data.get("type")
        if kind == "message_start":
            usage = (data.get("message") or {}).get("usage") or {}
            self.input_tokens = as_int(usage.get("input_tokens"))
        elif kind == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                self.parts.append(str(delta.get("text") or ""))
        elif kind == "message_delta":
            self.stop_reason = (data.get("delta") or {}).get("stop_reason") or self.stop_reason
            output = as_int((data.get("usage") or {}).get("output_tokens"))
            self.output_tokens = output if output is not None else self.output_tokens
        elif kind == "error":
            self.error = data.get("error") if isinstance(data.get("error"), dict) else {"message": str(data)}


async def _stream_once(body: dict, api_key: str, state: _StreamState, deadline: float) -> httpx.Response | None:
    """One streamed attempt. Returns the response when it failed before streaming, else None."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream("POST", f"{_BASE_URL}/messages", headers=_headers(api_key), json=body) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                return resp
            async for line in resp.aiter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[len("data:") :].strip())
                except ValueError:
                    continue
                if isinstance(data, dict):
                    state.read(data)
                    if state.error is not None:
                        return None
    return None


async def _streamed(body: dict, api_key: str) -> tuple[str, int | None, int | None, str | None]:
    """The streamed answer, retried within the transport policy: a 429 or 5xx before the stream, a
    dropped connection, or the provider's own transient error event mid-stream."""
    for attempt in range(MAX_ATTEMPTS):
        state = _StreamState()
        last = attempt == MAX_ATTEMPTS - 1
        try:
            failed = await _stream_once(body, api_key, state, time.monotonic() + STREAM_DEADLINE_SECONDS)
        except TimeoutError:
            raise ProviderError(
                f"the model's answer did not finish within bioAF's limit of {STREAM_DEADLINE_SECONDS:.0f} seconds",
                error_class="timeout",
                text=state.text,
                output_tokens=state.output_tokens,
                stop_reason=state.stop_reason,
            ) from None
        except httpx.HTTPError as exc:
            detail = _transport_detail(exc)
            if last:
                logger.warning("anthropic streamed submit transport failure: %s", detail)
                raise ProviderError(detail, error_class="transport") from exc
            logger.warning(
                "anthropic streamed submit transport failure (attempt %d), retrying: %s", attempt + 1, detail
            )
            await asyncio.sleep(backoff_seconds(attempt))
            continue
        if failed is not None:
            logger.warning("anthropic streamed submit non-2xx (%d): %s", failed.status_code, failed.text[:2000])
            if is_retryable(failed.status_code) and not last:
                await asyncio.sleep(backoff_seconds(attempt))
                continue
            _raise_for_status(failed)
        if state.error is not None:
            kind = str(state.error.get("type") or "error")
            message = str(state.error.get("message") or kind)
            if kind in _RETRYABLE_STREAM_ERRORS and not last:
                logger.warning("anthropic stream error event (attempt %d), retrying: %s", attempt + 1, message)
                await asyncio.sleep(backoff_seconds(attempt))
                continue
            raise ProviderError(message, error_class="rate_limit" if kind == "rate_limit_error" else "server")
        return state.text, state.output_tokens, state.input_tokens, state.stop_reason
    raise ProviderError("the streamed answer could not be obtained", error_class="transport")  # pragma: no cover


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
