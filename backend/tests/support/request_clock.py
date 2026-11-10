"""Deterministic clocks for request pacing and retry tests.

``ManualClock`` only moves when a test moves it. ``advance`` fires due sleepers
one wake time at a time, as an idle event loop would; ``jump`` moves straight
to the target and wakes every due sleeper at once, which is what a busy event
loop does to timers that were due while it was occupied (a delayed wakeup).

``AutoClock`` advances virtual time by exactly the requested amount whenever
something sleeps, so code that backs off or paces finishes immediately while
its observed timestamps stay exact. The autouse ``request_clock`` fixture
installs one for every test.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from datetime import UTC, datetime, timedelta

_WALL_START = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)


class ManualClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._start = start
        self.now = start
        self._sleepers: list[tuple[float, int, asyncio.Future]] = []
        self._seq = itertools.count()
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> datetime:
        return _WALL_START + timedelta(seconds=self.now - self._start)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + seconds, next(self._seq), future))
        await future

    @property
    def sleeping(self) -> int:
        return sum(1 for _, _, f in self._sleepers if not f.done())

    async def settle(self, turns: int = 20) -> None:
        for _ in range(turns):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        target = self.now + seconds
        while self._sleepers and self._sleepers[0][0] <= target:
            wake, _, future = heapq.heappop(self._sleepers)
            if future.done():
                continue
            self.now = max(self.now, wake)
            future.set_result(None)
            await self.settle()
        self.now = target
        await self.settle()

    async def jump(self, seconds: float) -> None:
        self.now += seconds
        due = []
        while self._sleepers and self._sleepers[0][0] <= self.now:
            due.append(heapq.heappop(self._sleepers)[2])
        for future in due:
            if not future.done():
                future.set_result(None)
        await self.settle()


class AutoClock(ManualClock):
    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if seconds > 0:
            self.now += seconds
        await asyncio.sleep(0)
