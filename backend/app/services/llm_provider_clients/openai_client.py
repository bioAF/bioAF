"""OpenAI provider client.

Uses the public REST API directly via httpx so we are not coupled to the
upstream SDK's version dance. We hit two endpoints:

    GET  https://api.openai.com/v1/models
    POST https://api.openai.com/v1/chat/completions
"""

from __future__ import annotations

import logging

import httpx

from app.services.llm_provider_clients import ProviderError

logger = logging.getLogger("bioaf.llm.openai")

_BASE_URL = "https://api.openai.com/v1"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


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
