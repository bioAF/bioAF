"""Outbound HTTP request policy: bounded attempts, retry delays, and budgets.

One ``send`` runs a single logical request under a named ``RequestPolicy``. It
owns attempt counting, the delay before a retry (a valid ``Retry-After``
hint, else bounded exponential backoff with jitter), and the operation's
deadline. Callers own everything else: authentication, redirects and trust,
what a final failure means ([], None, a partial result, an error), and any
quota wait, which they plug in through ``before_dispatch`` so it runs before
every attempt.

Only idempotent methods are ever retried; a POST under any policy is sent
once. Logs carry the caller's label, attempt counts, status or exception
class, and waits, never URLs (they can carry credentials) or bodies.
"""

from __future__ import annotations

import gzip
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger("bioaf.http")


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def wall(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)


SYSTEM_CLOCK = SystemClock()


class BudgetExhausted(TimeoutError):
    """The operation's deadline passed before a request could be sent."""


@dataclass(frozen=True)
class RequestPolicy:
    name: str
    max_attempts: int = 1
    retry_statuses: frozenset[int] = frozenset()
    retry_exceptions: tuple[type[Exception], ...] = ()
    # Bounds the computed backoff only. A provider's own retry hint is never
    # shortened; if it does not fit the remaining budget the operation ends.
    base_delay: float = 1.0
    max_delay: float = 8.0
    # The operation budget when the caller supplies no deadline.
    budget_seconds: float = 60.0
    retry_methods: frozenset[str] = field(default=frozenset({"GET", "HEAD"}))


_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})

# Literature Source reads: three attempts, connect and read failures included.
SOURCE_READ = RequestPolicy(
    name="source_read",
    max_attempts=3,
    retry_statuses=_TRANSIENT_STATUSES,
    retry_exceptions=(httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError),
    budget_seconds=180.0,
)
# Hosted model listing: a 300 s read timeout is not worth repeating for a list
# that has a built-in fallback, so only connect failures and statuses retry.
MODEL_LIST_READ = RequestPolicy(
    name="model_list_read",
    max_attempts=3,
    retry_statuses=_TRANSIENT_STATUSES,
    retry_exceptions=(httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError),
    budget_seconds=30.0,
)
# Public metadata reads (nf-core registry, pipeline schemas, GEO/ENA text):
# three attempts within a short budget; callers keep their own fallbacks.
METADATA_READ = RequestPolicy(
    name="metadata_read",
    max_attempts=3,
    retry_statuses=_TRANSIENT_STATUSES,
    retry_exceptions=(httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError),
    budget_seconds=90.0,
)
# Slack notification posts. POST is opted in deliberately: delivery is
# at-least-once (owner decision 2026-11-09), so a send that may have reached
# Slack is retried and a channel can occasionally see a duplicate.
SLACK_DELIVERY = RequestPolicy(
    name="slack_delivery",
    max_attempts=3,
    retry_statuses=frozenset({429, 500, 502, 503, 504}),
    retry_exceptions=(
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ),
    base_delay=2.0,
    max_delay=8.0,
    budget_seconds=60.0,
    retry_methods=frozenset({"POST"}),
)
# Paid inference and other non-repeatable requests: exactly one attempt.
SINGLE_ATTEMPT = RequestPolicy(name="single_attempt")


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Seconds to wait from a ``Retry-After`` value (delay-seconds or an HTTP
    date), or None when it is missing or unusable. A past date means zero."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        # Fractional, negative, or non-finite numbers are not delay-seconds.
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = (when - now).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _backoff(policy: RequestPolicy, failed_attempts: int, rng: Callable[[], float]) -> float:
    ceiling = min(policy.max_delay, policy.base_delay * 2 ** (failed_attempts - 1))
    return ceiling * (0.5 + 0.5 * rng())


async def fetch_text(url: str, *, label: str, timeout: float | httpx.Timeout = 30.0) -> str:
    """GET ``url`` under METADATA_READ, following redirects, and return its text,
    gunzipping a ``.gz`` URL's payload. Raises the final HTTP or transport
    error; whether that means "no data" is the caller's decision. Buffered:
    not for large downloads, and not a trusted-download boundary."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await read(client, url, policy=METADATA_READ, label=label)
        response.raise_for_status()
        if url.lower().endswith(".gz"):
            return gzip.decompress(response.content).decode("utf-8", errors="replace")
        return response.text


async def read(
    client: httpx.AsyncClient, url: str, *, policy: RequestPolicy, label: str, **kwargs: Any
) -> httpx.Response:
    """A GET under ``policy`` on the application's request clock."""
    from app.services import request_pacing

    return await send(client, "GET", url, policy=policy, label=label, clock=request_pacing.registry().clock, **kwargs)


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    policy: RequestPolicy,
    label: str,
    clock: Clock = SYSTEM_CLOCK,
    deadline: float | None = None,
    before_dispatch: Callable[[float], Awaitable[Any]] | None = None,
    on_throttle: Callable[[float], None] | None = None,
    retry_response: Callable[[httpx.Response], bool] | None = None,
    rng: Callable[[], float] = random.random,
    **request_kwargs: Any,
) -> httpx.Response:
    """Send one logical request under ``policy``.

    Returns the final response, retried or not; raises the final transport
    exception. ``deadline`` is a ``clock.monotonic()`` instant; a wait that
    would end past it is not started. ``before_dispatch(deadline)`` runs before
    every attempt (quota). ``on_throttle(seconds)`` reports each 429's wait so
    a shared quota can cool down. ``retry_response(response)`` lets a caller
    mark a protocol-level transient failure in an otherwise successful
    response (Slack's HTTP 200 ``ok: false``) as retryable."""
    started = clock.monotonic()
    if deadline is None:
        deadline = started + policy.budget_seconds
    retryable_method = method.upper() in policy.retry_methods
    max_attempts = policy.max_attempts if retryable_method else 1
    attempt = 0
    while True:
        attempt += 1
        if clock.monotonic() >= deadline:
            raise BudgetExhausted(f"{label}: budget exhausted before attempt {attempt}")
        if before_dispatch is not None:
            await before_dispatch(deadline)
        reason: str
        try:
            # The method helper (client.get, client.post) rather than client.request,
            # so callers' existing client doubles keep working.
            response = await getattr(client, method.lower())(url, **request_kwargs)
        except policy.retry_exceptions as exc:
            if attempt >= max_attempts:
                _log_final(label, policy, attempt, type(exc).__name__, started, clock)
                raise
            reason = type(exc).__name__
            delay = _backoff(policy, attempt, rng)
            if clock.monotonic() + delay > deadline:
                _log_final(label, policy, attempt, reason, started, clock)
                raise
        else:
            status = response.status_code
            hint = None
            if status == 429:
                hint = parse_retry_after(response.headers.get("Retry-After"), now=clock.wall())
            if status not in policy.retry_statuses and not (retry_response is not None and retry_response(response)):
                return response
            delay = hint if hint is not None else _backoff(policy, attempt, rng)
            if status == 429 and on_throttle is not None:
                on_throttle(delay)
            reason = f"status={status}"
            if attempt >= max_attempts or clock.monotonic() + delay > deadline:
                _log_final(label, policy, attempt, reason, started, clock)
                return response
            await response.aclose()
        logger.info(
            "http retry: label=%s policy=%s attempt=%d/%d reason=%s wait=%.2fs",
            label,
            policy.name,
            attempt,
            max_attempts,
            reason,
            delay,
        )
        await clock.sleep(delay)


def _log_final(label: str, policy: RequestPolicy, attempts: int, reason: str, started: float, clock: Clock) -> None:
    logger.warning(
        "http request ended unsuccessfully: label=%s policy=%s attempts=%d reason=%s elapsed=%.2fs",
        label,
        policy.name,
        attempts,
        reason,
        clock.monotonic() - started,
    )
