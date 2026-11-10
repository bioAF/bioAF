"""Per-bucket dispatch pacing and shared cooldown.

Every case runs on a ManualClock, so dispatch instants are exact and no
wall-clock sleep is the oracle. ``dispatch`` stands in for the HTTP send that
immediately follows a successful acquire.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import request_pacing
from app.services.request_pacing import PacingRegistry, QuotaWaitExceeded
from tests.support.request_clock import ManualClock

pytestmark = pytest.mark.asyncio


def _registry():
    clock = ManualClock()
    return clock, PacingRegistry(clock)


async def _dispatch(registry, key, interval, log, name, deadline=None):
    await registry.pacer(key).acquire(interval, deadline)
    log.append((name, registry.clock.monotonic()))


async def test_overlapping_operations_are_spaced_at_dispatch():
    clock, registry = _registry()
    log: list = []
    tasks = [asyncio.create_task(_dispatch(registry, "pubmed", 0.5, log, i)) for i in range(4)]
    await clock.settle()
    await clock.advance(5)
    await asyncio.gather(*tasks)
    times = [t for _, t in log]
    assert times[0] == clock._start
    assert all(b - a >= 0.5 for a, b in zip(times, times[1:]))
    assert times[-1] - times[0] == pytest.approx(1.5)


async def test_a_delayed_wakeup_does_not_release_a_catch_up_burst():
    clock, registry = _registry()
    log: list = []
    tasks = [asyncio.create_task(_dispatch(registry, "pubmed", 1.0, log, i)) for i in range(3)]
    await clock.settle()
    assert len(log) == 1
    # The loop was busy for 10 s: every waiter's timer is overdue at once.
    await clock.jump(10)
    times = [t for _, t in log]
    assert len(times) == 2, "only one overdue waiter may dispatch; the rest wait a full interval"
    await clock.advance(1.0)
    await asyncio.gather(*tasks)
    times = [t for _, t in log]
    assert all(b - a >= 1.0 for a, b in zip(times, times[1:]))


async def test_a_cooldown_extended_while_waiting_is_respected():
    clock, registry = _registry()
    pacer = registry.pacer("s2")
    log: list = []
    await _dispatch(registry, "s2", 1.0, log, "first")
    waiter = asyncio.create_task(_dispatch(registry, "s2", 1.0, log, "second"))
    await clock.settle()
    assert clock.sleeping == 1
    # A 429 elsewhere sets a 30 s cooldown after this waiter began waiting.
    pacer.cool_down(30.0)
    await clock.advance(1.0)
    assert [n for n, _ in log] == ["first"]
    await clock.advance(29.0)
    await waiter
    assert log[-1] == ("second", clock._start + 30.0)


async def test_a_shorter_cooldown_never_shortens_a_longer_one():
    clock, registry = _registry()
    pacer = registry.pacer("s2")
    pacer.cool_down(30.0)
    pacer.cool_down(5.0)
    log: list = []
    task = asyncio.create_task(_dispatch(registry, "s2", 0.1, log, "x"))
    await clock.advance(29.0)
    assert log == []
    await clock.advance(1.0)
    await task
    assert log == [("x", clock._start + 30.0)]


async def test_independent_buckets_progress_independently():
    clock, registry = _registry()
    log: list = []
    registry.pacer("semanticscholar").cool_down(300.0)
    blocked = asyncio.create_task(_dispatch(registry, "semanticscholar", 2.0, log, "s2"))
    for i in range(3):
        await _dispatch(registry, "europepmc", 0.0, log, f"epmc{i}")
    assert [n for n, _ in log] == ["epmc0", "epmc1", "epmc2"]
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked


async def test_a_wait_beyond_the_deadline_fails_without_dispatching():
    clock, registry = _registry()
    log: list = []
    await _dispatch(registry, "pubmed", 10.0, log, "first")
    with pytest.raises(QuotaWaitExceeded):
        await _dispatch(registry, "pubmed", 10.0, log, "late", deadline=clock.now + 5)
    assert [n for n, _ in log] == ["first"]
    assert clock.sleeping == 0


async def test_a_cancelled_waiter_releases_its_place_and_wastes_no_slot():
    clock, registry = _registry()
    log: list = []
    await _dispatch(registry, "pubmed", 1.0, log, "first")
    doomed = asyncio.create_task(_dispatch(registry, "pubmed", 1.0, log, "doomed"))
    survivor = asyncio.create_task(_dispatch(registry, "pubmed", 1.0, log, "survivor"))
    await clock.settle()
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed
    await clock.advance(1.0)
    await survivor
    # The survivor takes the next slot; the cancelled waiter consumed nothing.
    assert log == [("first", clock._start), ("survivor", clock._start + 1.0)]


async def test_each_caller_paces_at_its_own_interval_within_a_shared_bucket():
    clock, registry = _registry()
    log: list = []
    await _dispatch(registry, "pubmed", 1.0, log, "a")
    slow = asyncio.create_task(_dispatch(registry, "pubmed", 5.0, log, "slow"))
    await clock.advance(5.0)
    await slow
    assert log[-1] == ("slow", clock._start + 5.0)


async def test_the_registry_is_bound_to_one_event_loop_and_resets():
    first = request_pacing.registry()
    assert request_pacing.registry() is first
    replaced = request_pacing.reset(ManualClock())
    assert replaced is not first and request_pacing.registry() is replaced
    first.pacer("x").cool_down(10)
    assert replaced.pacer("x").cooldown_remaining() == 0
