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

A savepoint does not count as a commit: events deferred inside one wait for the outer commit. An
event deferred inside a savepoint that is rolled back is still published if the outer transaction
commits, because the queue belongs to the session, not to the savepoint. Do not defer from inside a
savepoint you expect to roll back.

The hooks are plain synchronous SQLAlchemy session events, which cannot await. They schedule the
emission on the running event loop; ``wait_for_deferred_events`` lets a caller (tests, mostly) wait
for what has been scheduled.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.services.event_bus import event_bus

logger = logging.getLogger("bioaf.post_commit_events")

_PENDING_KEY = "bioaf_pending_events"

# Strong references, so a scheduled emission is not garbage-collected before it runs.
_in_flight: set[asyncio.Task] = set()


def defer_event(session: AsyncSession | Session, event_type: str, payload: dict[str, Any]) -> None:
    """Queue ``event_type`` to be emitted after ``session`` commits; dropped if it does not."""
    sync_session = session.sync_session if isinstance(session, AsyncSession) else session
    sync_session.info.setdefault(_PENDING_KEY, []).append((event_type, payload))


async def wait_for_deferred_events() -> None:
    """Wait for every emission scheduled so far to finish."""
    while _in_flight:
        await asyncio.gather(*list(_in_flight), return_exceptions=True)


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    # SQLAlchemy also fires after_commit when a savepoint is released, while that savepoint is
    # still the session's current transaction. Only the outermost commit is durable.
    if session.in_nested_transaction():
        return
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Dropped %d committed event(s): no running event loop", len(pending))
        return
    for event_type, payload in pending:
        task = loop.create_task(event_bus.emit(event_type, payload))
        _in_flight.add(task)
        task.add_done_callback(_in_flight.discard)


@event.listens_for(Session, "after_transaction_end")
def _discard_when_transaction_ends(session: Session, transaction: SessionTransaction) -> None:
    # A commit has already taken its events in after_commit, so anything still queued when the
    # outermost transaction ends belongs to a rollback or a failed commit.
    if transaction.parent is None:
        session.info.pop(_PENDING_KEY, None)
