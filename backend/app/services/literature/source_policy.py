"""Resolved request policy for Literature Source traffic.

A ``SourceExecution`` is the configuration snapshot one operation runs under:
the source, whether it is enabled, its credential, and its effective dispatch
rate. Callers resolve it once (``sources_config_service.resolve``) and bind it
around the adapter call with ``bound``; adapters send every HTTP request
through ``get``, which paces it in the source's shared quota bucket, retries
under ``SOURCE_READ``, and stops at the bound operation deadline.

Quota buckets are per source and credential. Sources whose provider ignores
the key (bioRxiv, Europe PMC) have one service bucket. The bucket id holds a
hash of the key, never the key, and is never logged.

An adapter call with nothing bound (a caller with no organization context)
runs at the source default with the given key, in the same shared bucket.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx

from app.services import http_policy, request_pacing

logger = logging.getLogger("bioaf.literature.source_policy")


class SourceDisabled(Exception):
    """The source was turned off in Literature Sources settings."""


@dataclass(frozen=True)
class SourceExecution:
    source: str
    enabled: bool
    api_key: str | None = field(repr=False)
    requests_per_second: float
    override: int | None = None

    @property
    def bucket(self) -> tuple[str, str]:
        from app.services.literature.sources import get_adapter

        if self.api_key and getattr(get_adapter(self.source), "CREDENTIAL_SCOPED_QUOTA", False):
            digest = hashlib.sha256(f"{self.source}\0{self.api_key}".encode()).hexdigest()[:16]
            return (self.source, digest)
        return (self.source, "service")

    @property
    def interval(self) -> float:
        return 1.0 / self.requests_per_second if self.requests_per_second > 0 else 0.0


def effective_rate(source: str, has_api_key: bool, override: int | None) -> float:
    """A positive integer override replaces the adapter default; anything else
    (None, 0, a negative persisted value) leaves the default."""
    from app.services.literature.sources import get_adapter

    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return float(override)
    return get_adapter(source).get_rate_limit(has_api_key).requests_per_second


def default_execution(source: str, api_key: str | None = None) -> SourceExecution:
    return SourceExecution(
        source=source,
        enabled=True,
        api_key=api_key or None,
        requests_per_second=effective_rate(source, bool(api_key), None),
    )


@dataclass(frozen=True)
class _Binding:
    execution: SourceExecution
    deadline: float | None


_current: contextvars.ContextVar[_Binding | None] = contextvars.ContextVar("literature_source_execution", default=None)


@contextlib.contextmanager
def bound(execution: SourceExecution, *, budget_seconds: float | None = None) -> Iterator[None]:
    """Run adapter calls for ``execution.source`` under this snapshot. With a
    budget, queueing, subrequests, and backoff all count against it."""
    clock = request_pacing.registry().clock
    deadline = clock.monotonic() + budget_seconds if budget_seconds is not None else None
    token = _current.set(_Binding(execution, deadline))
    try:
        yield
    finally:
        _current.reset(token)


def _binding_for(source: str, api_key: str | None) -> _Binding:
    binding = _current.get()
    if binding is not None and binding.execution.source == source:
        return binding
    return _Binding(default_execution(source, api_key), None)


async def get(
    client: httpx.AsyncClient,
    url: str,
    *,
    source: str,
    api_key: str | None,
    label: str,
    **request_kwargs,
) -> httpx.Response:
    """One paced, retried GET for ``source``. Returns the final response;
    raises the final transport error, or a TimeoutError when the quota wait
    or the operation budget runs out. The adapter owns status/body handling."""
    binding = _binding_for(source, api_key)
    execution = binding.execution
    pacer_registry = request_pacing.registry()
    pacer = pacer_registry.pacer(execution.bucket)

    async def before_dispatch(deadline: float | None) -> None:
        waited = await pacer.acquire(execution.interval, deadline)
        if waited > 0:
            logger.debug("source request paced: label=%s waited=%.2fs", label, waited)

    def on_throttle(seconds: float) -> None:
        pacer.cool_down(seconds)
        logger.info("source throttled: label=%s cooldown=%.2fs", label, seconds)

    return await http_policy.send(
        client,
        "GET",
        url,
        policy=http_policy.SOURCE_READ,
        label=label,
        clock=pacer_registry.clock,
        deadline=binding.deadline,
        before_dispatch=before_dispatch,
        on_throttle=on_throttle,
        **request_kwargs,
    )
