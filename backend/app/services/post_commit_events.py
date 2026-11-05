"""Publish platform events only after the transaction they describe commits.

A service that changes state and wants to announce it calls ``defer_event`` on the session it wrote
with, instead of emitting straight away. The event waits in ``session.info`` and is handed to the
event bus once that session's outermost transaction commits. If the transaction rolls back, or the
commit fails, the waiting events are dropped with it, so nothing announces an outcome the database
does not hold, and nothing deferred in one transaction can be published by a later one on the same
session.

Delivery is best effort and in-process, exactly as the event bus already is: a process that dies
between the commit and the emission loses the event, and there is no outbox or retry. The service
that owns the transaction does not need to do anything after commit; the session hooks below do it.

Savepoints are honoured. An event belongs to the innermost savepoint open when it was deferred.
Releasing that savepoint hands the event to the enclosing savepoint (or the outer transaction), and
it is published only when the outer transaction commits. Rolling the savepoint back drops it, along
with anything released into it, while events of the enclosing transaction are kept.

The hooks are plain synchronous SQLAlchemy session events, which cannot await. They schedule the
emission on the running event loop; ``wait_for_deferred_events`` lets a caller (tests, mostly) wait
for what has been scheduled.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.services.event_bus import event_bus

logger = logging.getLogger("bioaf.post_commit_events")

_PENDING_KEY = "bioaf_pending_events"


@dataclass
class _Pending:
    owner: SessionTransaction | None
    event_type: str
    payload: dict[str, Any]


# Strong references, so a scheduled emission is not garbage-collected before it runs.
_in_flight: set[asyncio.Task[None]] = set()


def defer_event(session: AsyncSession | Session, event_type: str, payload: dict[str, Any]) -> None:
    """Queue ``event_type`` to be emitted after ``session`` commits; dropped if it does not."""
    sync_session = session.sync_session if isinstance(session, AsyncSession) else session
    # Owned by the innermost open savepoint, or by the outer transaction (None).
    owner = sync_session.get_nested_transaction()
    sync_session.info.setdefault(_PENDING_KEY, []).append(_Pending(owner, event_type, payload))


async def wait_for_deferred_events() -> None:
    """Wait for every emission scheduled on this event loop so far to finish.

    Tasks from another loop (a closed test loop, say) are skipped: they can never finish here.
    """
    loop = asyncio.get_running_loop()
    while pending := [t for t in _in_flight if t.get_loop() is loop and not t.done()]:
        await asyncio.gather(*pending, return_exceptions=True)


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    pending: list[_Pending] = session.info.get(_PENDING_KEY) or []
    # SQLAlchemy also fires after_commit when a savepoint is released, while that savepoint is
    # still the session's innermost transaction. Its events move to the enclosing transaction and
    # wait for the outer commit.
    savepoint = session.get_nested_transaction()
    if savepoint is not None:
        parent = savepoint.parent
        heir = parent if parent is not None and parent.nested else None
        for item in pending:
            if item.owner is savepoint:
                item.owner = heir
        return
    session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Dropped %d committed event(s): no running event loop", len(pending))
        return
    for item in pending:
        task = loop.create_task(event_bus.emit(item.event_type, item.payload))
        _in_flight.add(task)
        task.add_done_callback(_in_flight.discard)


@event.listens_for(Session, "after_transaction_end")
def _discard_when_transaction_ends(session: Session, transaction: SessionTransaction) -> None:
    if transaction.nested:
        # A released savepoint has already handed its events on, so anything it still owns was
        # deferred in a savepoint that rolled back.
        pending: list[_Pending] = session.info.get(_PENDING_KEY) or []
        session.info[_PENDING_KEY] = [item for item in pending if item.owner is not transaction]
    elif transaction.parent is None:
        # A commit has already taken its events in after_commit, so anything still queued when the
        # outermost transaction ends belongs to a rollback or a failed commit.
        session.info.pop(_PENDING_KEY, None)
