"""What an event subscriber on its own database session sees when an event reaches it.

A real subscriber (the notification router, the custom-pipeline cascade, the webhook dispatcher) opens
a fresh session and reads the rows the event names. ``CommittedView`` does the same for tests: on
each event it runs ``read(session, payload)`` on an independent session and records the result, so a
test can assert both that the event arrived and what was committed when it did.

    view = CommittedView([DATA_UPLOADED], read=lambda s, p: s.get(File, p["entity_id"]))
    ...mutate (no commit)...
    await view.settle()
    assert view.events == []          # nothing announced before the commit
    await session.commit()
    await view.settle()
    [(payload, row)] = view.seen(DATA_UPLOADED)
    view.close()
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app import database as database_module
from app.services.event_bus import event_bus
from app.services.post_commit_events import wait_for_deferred_events

Reader = Callable[[Any, dict[str, Any]], Awaitable[Any]]


async def _nothing(_session, _payload) -> None:
    return None


class CommittedView:
    def __init__(self, event_types: list[str], read: Reader = _nothing) -> None:
        self.events: list[tuple[str, dict[str, Any], Any]] = []
        self._callbacks = []
        for event_type in event_types:

            async def record(payload, _event_type=event_type):
                async with database_module.async_session_factory() as s:
                    observed = await read(s, payload)
                self.events.append((_event_type, payload, observed))

            event_bus.subscribe(event_type, record)
            self._callbacks.append((event_type, record))

    async def settle(self) -> None:
        """Let every emission scheduled so far run to completion."""
        await wait_for_deferred_events()

    def seen(self, event_type: str) -> list[tuple[dict[str, Any], Any]]:
        return [(payload, observed) for kind, payload, observed in self.events if kind == event_type]

    def payloads(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for payload, _ in self.seen(event_type)]

    def close(self) -> None:
        for event_type, callback in self._callbacks:
            event_bus.unsubscribe(event_type, callback)
