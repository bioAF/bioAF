"""Google Gemini provider client."""

from __future__ import annotations

import logging

import httpx

from app.services.llm_provider_clients import ProviderError

logger = logging.getLogger("bioaf.llm.google")

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


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
