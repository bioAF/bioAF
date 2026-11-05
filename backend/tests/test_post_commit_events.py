"""Events about database outcomes are published only once those outcomes are committed.

Best effort: an event deferred on a session is emitted after that session's transaction commits,
and discarded if it rolls back or the commit fails. A discarded event never leaks into a later
transaction on the same session. There is no durable retry.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.organization import Organization
from app.services.event_bus import event_bus
from app.services.post_commit_events import defer_event, wait_for_deferred_events

EVENT = "test.post_commit"


@pytest_asyncio.fixture
async def received():
    event_bus.clear()
    seen: list[dict] = []

    async def record(payload):
        seen.append(payload)

    event_bus.subscribe(EVENT, record)
    yield seen
    event_bus.clear()


@pytest.mark.asyncio
async def test_a_deferred_event_is_emitted_only_after_commit(session, received):
    session.add(Organization(name="Deferred Org"))
    await session.flush()
    defer_event(session, EVENT, {"n": 1})

    await wait_for_deferred_events()
    assert received == []

    await session.commit()
    await wait_for_deferred_events()
    assert received == [{"n": 1}]


@pytest.mark.asyncio
async def test_a_rolled_back_event_is_discarded_and_never_emitted_by_a_later_commit(session, received):
    session.add(Organization(name="Rolled Back Org"))
    await session.flush()
    defer_event(session, EVENT, {"n": "discarded"})
    await session.rollback()

    session.add(Organization(name="Later Org"))
    await session.flush()
    defer_event(session, EVENT, {"n": "later"})
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": "later"}]


@pytest.mark.asyncio
async def test_a_failed_commit_emits_nothing_and_leaks_nothing(session, received):
    await session.execute(text("SELECT 1"))
    defer_event(session, EVENT, {"n": "failed commit"})
    # A row that fails at flush time inside commit: organizations.name is NOT NULL.
    session.add(Organization(name=None))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()
    await wait_for_deferred_events()
    assert received == []

    await session.execute(text("SELECT 1"))
    await session.commit()
    await wait_for_deferred_events()
    assert received == []


@pytest.mark.asyncio
async def test_a_released_savepoint_does_not_publish_early(session, received):
    await session.execute(text("SELECT 1"))
    async with session.begin_nested():
        defer_event(session, EVENT, {"n": "in savepoint"})
    await wait_for_deferred_events()
    assert received == []

    await session.commit()
    await wait_for_deferred_events()
    assert received == [{"n": "in savepoint"}]


@pytest.mark.asyncio
async def test_a_failing_subscriber_does_not_undo_the_commit_or_block_others(session, received):
    async def explode(payload):
        raise RuntimeError("subscriber down")

    event_bus.subscribe(EVENT, explode)
    session.add(Organization(name="Committed Org"))
    await session.flush()
    defer_event(session, EVENT, {"n": 2})
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": 2}]
    count = (await session.execute(text("SELECT count(*) FROM organizations WHERE name = 'Committed Org'"))).scalar()
    assert count == 1


# --- Savepoints: only a committed savepoint's events reach the outer commit ---


@pytest.mark.asyncio
async def test_a_rolled_back_savepoint_drops_its_events_and_keeps_the_outer_ones(session, received):
    await session.execute(text("SELECT 1"))
    defer_event(session, EVENT, {"n": "outer before"})
    savepoint = await session.begin_nested()
    defer_event(session, EVENT, {"n": "in rolled-back savepoint"})
    await savepoint.rollback()
    defer_event(session, EVENT, {"n": "outer after"})
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": "outer before"}, {"n": "outer after"}]


@pytest.mark.asyncio
async def test_a_rolled_back_inner_savepoint_inside_a_released_one(session, received):
    await session.execute(text("SELECT 1"))
    defer_event(session, EVENT, {"n": "outer"})
    outer_sp = await session.begin_nested()
    defer_event(session, EVENT, {"n": "released savepoint"})
    inner_sp = await session.begin_nested()
    defer_event(session, EVENT, {"n": "rolled-back inner"})
    await inner_sp.rollback()
    await outer_sp.commit()
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": "outer"}, {"n": "released savepoint"}]


@pytest.mark.asyncio
async def test_a_released_savepoint_inside_a_rolled_back_one_is_dropped_with_it(session, received):
    await session.execute(text("SELECT 1"))
    defer_event(session, EVENT, {"n": "outer"})
    outer_sp = await session.begin_nested()
    inner_sp = await session.begin_nested()
    defer_event(session, EVENT, {"n": "released inside rolled-back"})
    await inner_sp.commit()
    await outer_sp.rollback()
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": "outer"}]


@pytest.mark.asyncio
async def test_a_savepoint_rolled_back_by_an_exception_drops_its_events(session, received):
    await session.execute(text("SELECT 1"))
    with pytest.raises(RuntimeError):
        async with session.begin_nested():
            defer_event(session, EVENT, {"n": "doomed"})
            raise RuntimeError("abandon the savepoint")
    defer_event(session, EVENT, {"n": "kept"})
    await session.commit()
    await wait_for_deferred_events()

    assert received == [{"n": "kept"}]
