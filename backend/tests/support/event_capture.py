"""Record what the event bus publishes, for tests asserting post-commit events.

captured = EventCapture([INTEGRATION_FILE_READY])
...commit...
await captured.settle()
assert captured.of(INTEGRATION_FILE_READY) == [...]
"""

from __future__ import annotations

from typing import Any

from app.services.event_bus import event_bus
from app.services.post_commit_events import wait_for_deferred_events


class EventCapture:
    def __init__(self, event_types: list[str]) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._callbacks = []
        for event_type in event_types:

            async def record(payload, _event_type=event_type):
                self.events.append((_event_type, payload))

            event_bus.subscribe(event_type, record)
            self._callbacks.append((event_type, record))

    async def settle(self) -> None:
        await wait_for_deferred_events()

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.events if kind == event_type]

    def close(self) -> None:
        for event_type, callback in self._callbacks:
            event_bus.unsubscribe(event_type, callback)
