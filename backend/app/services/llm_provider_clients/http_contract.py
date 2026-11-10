"""Shared HTTP failure and JSON-root handling for the hosted provider clients.

Status classification, transport metadata, the JSON-object check, and safe
log lines are the same for every hosted provider. Error-code allowlists and
response envelopes stay in each client: only the client knows its protocol.

Logs from here carry only controlled fields (provider, operation, status,
allowlisted code, exception class, timeout); never provider bodies, request
URLs, or exception text, which can embed keys (Google sends its key as a
query parameter).
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable

from typing import Any

import httpx

from app.services import http_policy, request_pacing
from app.services.llm_provider_clients import ProviderError

logger = logging.getLogger("bioaf.llm.http")

# Upstream statuses that mean the provider gave up waiting.
_TIMEOUT_STATUSES = (408, 504)


async def send(
    client: httpx.AsyncClient, method: str, url: str, *, provider: str, operation: str, **kwargs: Any
) -> httpx.Response:
    """Model listing is a read and retries transient failures; every other
    provider request (paid completions, tool turns) is sent exactly once."""
    policy = http_policy.MODEL_LIST_READ if operation == "list_models" else http_policy.SINGLE_ATTEMPT
    try:
        return await http_policy.send(
            client,
            method,
            url,
            policy=policy,
            label=f"{provider}.{operation}",
            clock=request_pacing.registry().clock,
            **kwargs,
        )
    except http_policy.BudgetExhausted as exc:
        raise ProviderError(
            f"{provider} request budget ran out", error_class="transport", timeout=True, provider=provider
        ) from exc


def status_error_class(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    return "other"


def allowlisted_code(resp: httpx.Response, keys: Iterable[str], allowed: Collection[str]) -> str | None:
    """The provider's ``error.<key>`` value, only when it is an exact allowlisted
    identifier. A value merely shaped like a code can still be an account id."""
    try:
        error = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict):
        return None
    for key in keys:
        value = error.get(key)
        if isinstance(value, str) and value in allowed:
            return value
    return None


def raise_for_status(
    resp: httpx.Response,
    *,
    provider: str,
    operation: str,
    upstream_code: str | None,
    message: str | None = None,
) -> None:
    """Raise a classified ProviderError for a non-2xx response.

    The message defaults to the provider's own error text, which the failed
    Agent Review card shows to the user; it is never logged."""
    status = resp.status_code
    if status < 400:
        return
    error_class = status_error_class(status)
    logger.warning(
        "provider request failed: provider=%s operation=%s upstream_status=%d upstream_code=%s error_class=%s",
        provider,
        operation,
        status,
        upstream_code or "none",
        error_class,
    )
    text = message or resp.text.strip() or f"{provider} returned HTTP {status}"
    raise ProviderError(
        text,
        error_class=error_class,
        upstream_status=status,
        upstream_code=upstream_code,
        timeout=status in _TIMEOUT_STATUSES,
        provider=provider,
    )


def transport_error(exc: httpx.HTTPError, *, provider: str, label: str, operation: str) -> ProviderError:
    """A transport-class ProviderError naming only the exception class. httpx
    exception text can include the request URL."""
    timeout = isinstance(exc, httpx.TimeoutException)
    kind = type(exc).__name__
    logger.warning(
        "provider transport failure: provider=%s operation=%s exception_type=%s timeout=%s",
        provider,
        operation,
        kind,
        str(timeout).lower(),
    )
    message = f"{label} request timed out ({kind})" if timeout else f"{label} transport failure: {kind}"
    return ProviderError(message, error_class="transport", timeout=timeout, provider=provider)


def parse_error(message: str, *, provider: str) -> ProviderError:
    return ProviderError(message, error_class="parse", provider=provider)


def json_object(resp: httpx.Response, *, provider: str, label: str, what: str) -> dict:
    """The response body as a JSON object, or a parse-class ProviderError."""
    try:
        data = resp.json()
    except ValueError as exc:
        raise parse_error(f"{label} {what} response is not valid JSON", provider=provider) from exc
    if not isinstance(data, dict):
        raise parse_error(f"{label} {what} response is not a JSON object", provider=provider)
    return data


def object_list(value: object, *, provider: str, label: str, what: str) -> list[dict]:
    """A list whose every item is a JSON object, or a parse-class ProviderError."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise parse_error(f"{label} {what} is not a list of objects", provider=provider)
    return value


def optional_text(value: object, *, provider: str, label: str, what: str) -> str | None:
    """A protocol field that is absent (None) or a string; anything else is a
    parse-class ProviderError, never a TypeError from a later lookup."""
    if value is None or isinstance(value, str):
        return value
    raise parse_error(f"{label} {what} is not text", provider=provider)


def incomplete(message: str, *, provider: str, reason: str | None) -> ProviderError:
    """The provider stopped before a usable answer (truncation, content filter)."""
    return ProviderError(message, error_class="incomplete", upstream_code=reason, provider=provider)
