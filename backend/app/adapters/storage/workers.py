"""Storage-changing provider calls, tracked until their threads actually stop (storage lifecycle 11G).

Every SDK call that writes, moves or deletes objects runs in a worker thread. Cancelling the task
that awaits it does not stop the thread: the call goes on changing storage. Each such call is
registered here from the moment its thread starts until the thread returns, whatever happens to
the awaiting task, so storage destroy never takes a cancelled upload or background write for a
finished one, and an infrastructure operation never releases its lock while its own call runs.

The registry lives at the adapter boundary because that is where the threads start. It records the
infrastructure operation the call was made under through a lookup the operations service
installs (adapters do not import services).
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class StorageWorker:
    label: str
    operation_id: int | None
    started_at: datetime
    # The writer claim (upload, background write) the call was made under, if any.
    claim: str | None = None


_running: dict[int, StorageWorker] = {}
_ids = itertools.count(1)
_operation_lookup: Callable[[], int | None] = lambda: None  # noqa: E731
_claim_lookup: Callable[[], str | None] = lambda: None  # noqa: E731
_on_settled: list[Callable[[StorageWorker], None]] = []


def set_operation_lookup(lookup: Callable[[], int | None]) -> None:
    """Installed by the operations service: which operation the current task works for."""
    global _operation_lookup
    _operation_lookup = lookup


def set_claim_lookup(lookup: Callable[[], str | None]) -> None:
    """Installed by write admission: which writer claim the current task holds."""
    global _claim_lookup
    _claim_lookup = lookup


def on_settled(callback: Callable[[StorageWorker], None]) -> None:
    """Called (on the event loop) each time a tracked call's thread returns."""
    _on_settled.append(callback)


async def run(label: str, fn: Callable[..., Any], *args: Any) -> Any:
    """Run a storage-changing call in a thread, tracked until the thread itself returns."""
    return await track(label, asyncio.to_thread(fn, *args))


async def track(label: str, work: Coroutine[Any, Any, Any]) -> Any:
    """Run ``work`` (a storage change, and anything that must finish with it) tracked to the end.

    It runs as owned, non-cancellable work: cancelling the caller stops the wait, not the work,
    and the registration (and server shutdown) lasts until it returns. A caller that pairs a
    deletion with recording it passes both as one ``work``, so an operation cannot settle between.
    """
    from app import runtime

    key = next(_ids)
    worker = StorageWorker(label, _operation_lookup(), datetime.now(timezone.utc), _claim_lookup())
    _running[key] = worker

    async def tracked() -> Any:
        try:
            return await work
        finally:
            _running.pop(key, None)
            for callback in list(_on_settled):
                callback(worker)

    return await runtime.shielded(tracked(), role="storage-call")


def running(operation_id: int | None = None) -> list[StorageWorker]:
    """Calls whose threads are still running; only those made under ``operation_id`` when given."""
    workers = list(_running.values())
    if operation_id is not None:
        workers = [w for w in workers if w.operation_id == operation_id]
    return workers
