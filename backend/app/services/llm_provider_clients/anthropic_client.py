"""Anthropic Claude provider client."""

from __future__ import annotations

import logging

import httpx

from app.services.llm_provider_clients import ProviderError

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
