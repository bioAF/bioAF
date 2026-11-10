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
    rng: Callable[[], float] = random.random,
    **request_kwargs: Any,
) -> httpx.Response:
    """Send one logical request under ``policy``.

    Returns the final response, retried or not; raises the final transport
    exception. ``deadline`` is a ``clock.monotonic()`` instant; a wait that
    would end past it is not started. ``before_dispatch(deadline)`` runs before
    every attempt (quota). ``on_throttle(seconds)`` reports each 429's wait so
    a shared quota can cool down."""
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
            response = await client.request(method, url, **request_kwargs)
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
            if status not in policy.retry_statuses:
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
