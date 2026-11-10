"""Dispatch pacing and shared cooldown per quota bucket.

A ``Pacer`` spaces sends in one bucket (for example one Literature Source and
credential) by a minimum interval measured from the previous actual dispatch,
and holds every sender back while a cooldown (set from a 429) is in force.
The decision is made at the moment of sending: a waiter that wakes late
rechecks both the last dispatch and the cooldown, so overdue waiters never
release as a burst and a cooldown extended mid-wait is honored. A cancelled
or expired waiter has claimed nothing, so it wastes no slot.

Waiters are not queued in order; under contention any ready waiter may take
the next slot, and every wait is bounded by its operation's deadline.

The registry is process-local and bound to one event loop: it paces this
application instance only, not other workers, replicas, installations, or
other software sharing a key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Hashable

from app.services.http_policy import SYSTEM_CLOCK, Clock


class QuotaWaitExceeded(TimeoutError):
    """The next permitted dispatch is later than the operation's deadline."""


class Pacer:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._last_dispatch: float | None = None
        self._cooldown_until = float("-inf")

    def cool_down(self, seconds: float) -> None:
        """Hold every sender for ``seconds`` from now; never shortens a longer cooldown."""
        self._cooldown_until = max(self._cooldown_until, self._clock.monotonic() + seconds)

    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - self._clock.monotonic())

    async def acquire(self, interval: float, deadline: float | None = None) -> float:
        """Wait until a send is permitted, then claim it. Returns the seconds waited."""
        started = self._clock.monotonic()
        while True:
            now = self._clock.monotonic()
            ready = self._cooldown_until
            if self._last_dispatch is not None:
                ready = max(ready, self._last_dispatch + interval)
            if now >= ready:
                self._last_dispatch = now
                return now - started
            if deadline is not None and ready > deadline:
                raise QuotaWaitExceeded("next permitted dispatch is past the operation deadline")
            await self._clock.sleep(ready - now)


class PacingRegistry:
    def __init__(self, clock: Clock = SYSTEM_CLOCK) -> None:
        self.clock = clock
        self._pacers: dict[Hashable, Pacer] = {}

    def pacer(self, key: Hashable) -> Pacer:
        pacer = self._pacers.get(key)
        if pacer is None:
            pacer = self._pacers[key] = Pacer(self.clock)
        return pacer


_registry: PacingRegistry | None = None
_registry_loop: asyncio.AbstractEventLoop | None = None


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def registry() -> PacingRegistry:
    """The registry for the running event loop. A new loop (a new lifespan or
    test) gets a fresh registry, so no pacing state crosses loops."""
    global _registry, _registry_loop
    loop = _running_loop()
    if _registry is not None and _registry_loop is None:
        # Reset outside any loop (a synchronous fixture): the first loop adopts it.
        _registry_loop = loop
    if _registry is None or _registry_loop is not loop:
        _registry = PacingRegistry()
        _registry_loop = loop
    return _registry


def reset(clock: Clock = SYSTEM_CLOCK) -> PacingRegistry:
    """Start from empty pacing state (application startup, tests)."""
    global _registry, _registry_loop
    _registry = PacingRegistry(clock)
    _registry_loop = _running_loop()
    return _registry
