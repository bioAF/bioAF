"""The shared periodic scheduler (plan 14).

A ``PeriodicJob`` says when to run and how to report a failure; its iteration does the work and
owns its sessions, commits, rollbacks, and persisted schedules. ``run_periodic`` waits the first
delay, runs one iteration at a time, and counts the next delay from when the previous one settled.
A failed iteration is reported through the job's own message and the next one runs on the existing
interval. A stop request ends the job between iterations (an iteration in progress finishes first);
cancellation propagates.

``prepare`` runs once when the job starts and returns the iteration: it binds anything the job
held for its lifetime (the session factory current at start, loop-local state such as the plot
archive's one-time backfill).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# The loops' existing logger, so each job's error line reads exactly as before.
logger = logging.getLogger("bioaf")
lifecycle = logging.getLogger("bioaf.runtime")

Iteration = Callable[[], Awaitable[object]]
Wait = Callable[[float, "asyncio.Event | None"], Awaitable[bool]]


@dataclass(frozen=True)
class PeriodicJob:
    role: str
    prepare: Callable[[], Iteration]
    interval: float
    first_delay: float
    error_message: str
    enabled: Callable[[], bool] | None = None


async def wait_or_stop(delay: float, stopping: asyncio.Event | None) -> bool:
    """Sleep ``delay`` seconds; True if ``stopping`` was set first (or already)."""
    if stopping is None:
        await asyncio.sleep(delay)
        return False
    if stopping.is_set():
        return True
    try:
        await asyncio.wait_for(stopping.wait(), timeout=delay)
        return True
    except TimeoutError:
        return False


async def run_periodic(job: PeriodicJob, *, stopping: asyncio.Event | None = None, wait: Wait = wait_or_stop) -> None:
    if job.enabled is not None and not job.enabled():
        lifecycle.info("task role=%s transition=disabled", job.role)
        return
    iteration = job.prepare()
    delay = job.first_delay
    while True:
        if await wait(delay, stopping):
            return
        try:
            await iteration()
        except Exception as e:
            logger.error(job.error_message, e)
        delay = job.interval
