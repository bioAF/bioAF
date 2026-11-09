"""In-process event bus with async pub/sub for platform events."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

from app import runtime

logger = logging.getLogger("bioaf.event_bus")

EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Simple in-process observer pattern using asyncio.

    Subscribers register callbacks for specific event types.
    When an event is emitted, all subscribers for that type are invoked.
    One failing subscriber does not block others.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: EventCallback) -> None:
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        """Remove one registration of ``callback``; a no-op if it is not subscribed."""
        callbacks = self._subscribers.get(event_type)
        if callbacks and callback in callbacks:
            callbacks.remove(callback)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        callbacks = self._subscribers.get(event_type, [])
        if not callbacks:
            logger.debug("No subscribers for event %s", event_type)
            return

        logger.info("Emitting event %s to %d subscriber(s)", event_type, len(callbacks))
        # Each subscriber call is a child of the emission: owned (and awaited by shutdown) when an
        # owner is bound, so a cancelled emission cannot leave a subscriber running unseen.
        tasks = [
            runtime.spawn(self._safe_invoke(cb, event_type, payload), role=f"event:{event_type}:subscriber", event=True)
            for cb in callbacks
        ]
        await asyncio.gather(*tasks)

    async def _safe_invoke(self, callback: EventCallback, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await callback(payload)
        except Exception:
            logger.exception("Subscriber %s failed for event %s", callback.__qualname__, event_type)

    def clear(self) -> None:
        self._subscribers.clear()


# Singleton instance
event_bus = EventBus()
