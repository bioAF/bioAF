"""The application task owner (plan 14, delivery B).

One owner per application lifespan (or test) holds every task it admits: strong references, named
roles, observed outcomes, independent failures, closed admission, and a shutdown bounded by shared
deadlines. Its lifecycle log lines carry only controlled fields: never the exception text, the
coroutine's arguments, or a traceback.
"""

import asyncio
import gc
import logging

import pytest

from app import runtime
from app.runtime import OwnerClosed, TaskOwner

pytestmark = pytest.mark.asyncio


async def _forever():
    await asyncio.Event().wait()


async def test_an_admitted_task_survives_garbage_collection_and_leaves_once_finished():
    owner = TaskOwner()
    done = asyncio.Event()
    release = asyncio.Event()

    async def job():
        await release.wait()
        done.set()

    owner.spawn(job(), role="job")
    gc.collect()
    assert owner.roles() == ["job"]
    release.set()
    await done.wait()
    await asyncio.sleep(0)
    assert owner.roles() == []


async def test_a_failure_is_logged_with_controlled_fields_only(caplog):
    owner = TaskOwner()
    secret = "postgresql://bioaf:hunter2@db/bioaf"

    async def job(url):
        raise ValueError(f"cannot reach {url}")

    with caplog.at_level(logging.DEBUG, logger="bioaf.runtime"):
        task = owner.spawn(job(secret), role="billing-sync")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    failed = [r for r in caplog.records if getattr(r, "lifecycle_outcome", None) == "failed"]
    assert len(failed) == 1
    record = failed[0]
    assert record.levelno == logging.ERROR
    assert record.lifecycle_role == "billing-sync"
    assert record.lifecycle_exception_type == "ValueError"
    assert record.exc_info is None
    text = record.getMessage()
    assert "billing-sync" in text and "ValueError" in text
    assert "hunter2" not in text and "cannot reach" not in text
    assert all("hunter2" not in r.getMessage() for r in caplog.records)


async def test_one_failure_does_not_cancel_its_peers():
    owner = TaskOwner()

    async def boom():
        raise RuntimeError("x")

    peer = owner.spawn(_forever(), role="peer")
    failing = owner.spawn(boom(), role="boom")
    await asyncio.gather(failing, return_exceptions=True)
    await asyncio.sleep(0)
    assert not peer.done()
    assert owner.roles() == ["peer"]
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_a_closed_owner_refuses_new_work_without_starting_it():
    owner = TaskOwner()
    owner.close_admission()
    ran = []

    async def job():
        ran.append(True)

    coro = job()
    with pytest.raises(OwnerClosed):
        owner.spawn(coro, role="late")
    await asyncio.sleep(0)
    assert ran == []
    assert coro.cr_frame is None  # closed, so no "never awaited" warning


async def test_events_are_admitted_until_event_admission_closes():
    owner = TaskOwner()
    owner.close_admission()
    seen = []

    async def emit():
        seen.append(True)

    await owner.spawn(emit(), role="event:x", event=True)
    assert seen == [True]
    owner.close_event_admission()
    coro = emit()
    with pytest.raises(OwnerClosed):
        owner.spawn(coro, role="event:y", event=True)


async def test_shutdown_lets_finishing_work_settle_then_cancels_the_rest():
    owner = TaskOwner()
    finished = []

    async def quick():
        await asyncio.sleep(0.01)
        finished.append("quick")

    owner.spawn(quick(), role="quick")
    stuck = owner.spawn(_forever(), role="stuck")

    report = await owner.shutdown(drain_seconds=0.1, cancel_seconds=1)

    assert finished == ["quick"]
    assert stuck.cancelled()
    assert report.clean
    assert report.cancelled == ["stuck"]
    assert report.uncooperative == []
    assert owner.roles() == []


async def test_children_spawned_while_draining_are_owned_and_settled():
    owner = TaskOwner()
    child_done = []

    async def child():
        await asyncio.sleep(0.01)
        child_done.append(True)

    async def parent():
        await asyncio.sleep(0.01)
        runtime.spawn(child(), role="child")

    owner.spawn(parent(), role="parent")
    report = await owner.shutdown(drain_seconds=1, cancel_seconds=1)
    assert child_done == [True]
    assert report.clean and report.cancelled == []


async def test_work_that_refuses_cancellation_is_reported_without_waiting_forever():
    owner = TaskOwner()
    stubborn_release = asyncio.Event()

    async def stubborn():
        while not stubborn_release.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                continue

    owner.spawn(stubborn(), role="stubborn")
    loop = asyncio.get_running_loop()
    started = loop.time()
    report = await owner.shutdown(drain_seconds=0.05, cancel_seconds=0.2)
    elapsed = loop.time() - started

    assert elapsed < 1.0
    assert not report.clean
    assert report.uncooperative == ["stubborn"]
    stubborn_release.set()
    await asyncio.sleep(0.05)


async def test_a_task_that_swallows_one_cancellation_is_cancelled_again():
    owner = TaskOwner()

    async def once():
        try:
            await _forever()
        except asyncio.CancelledError:
            pass
        await _forever()

    task = owner.spawn(once(), role="once")
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=1)
    assert task.cancelled()
    assert report.clean


async def test_spawn_follows_the_bound_owner_and_owners_are_independent():
    first, second = TaskOwner(), TaskOwner()

    with runtime.bound(first):
        a = runtime.spawn(_forever(), role="a")
    with runtime.bound(second):
        b = runtime.spawn(_forever(), role="b")

    assert first.roles() == ["a"] and second.roles() == ["b"]
    await first.shutdown(drain_seconds=0, cancel_seconds=1)
    assert a.cancelled() and not b.done()
    await second.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_a_task_spawned_by_an_owned_task_belongs_to_the_same_owner():
    owner = TaskOwner()
    spawned = asyncio.Event()

    async def parent():
        runtime.spawn(_forever(), role="grandchild")
        spawned.set()

    owner.spawn(parent(), role="parent")
    await spawned.wait()
    await asyncio.sleep(0)
    assert owner.roles() == ["grandchild"]
    await owner.shutdown(drain_seconds=0, cancel_seconds=1)


async def test_without_a_bound_owner_spawn_keeps_the_legacy_unowned_task():
    assert runtime.current_owner() is None
    ran = asyncio.Event()

    async def job():
        ran.set()

    task = runtime.spawn(job(), role="legacy")
    await ran.wait()
    assert isinstance(task, asyncio.Task)


async def test_an_owner_bound_to_a_session_is_found_by_its_commit_hook(db_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    owner = TaskOwner()
    factory = runtime.owned_sessions(async_sessionmaker(db_engine, class_=AsyncSession), owner)
    async with factory() as first, factory() as second:
        assert runtime.session_owner(first.sync_session) is owner
        assert first.sync_session.info is not second.sync_session.info
    with runtime.bound(None):  # the database fixture binds the test's own owner
        async with async_sessionmaker(db_engine, class_=AsyncSession)() as plain:
            assert runtime.session_owner(plain.sync_session) is None


async def test_a_worker_thread_starts_owned_work_until_admission_closes():
    owner = TaskOwner()
    loop = asyncio.get_running_loop()
    seen = []

    async def write(n):
        seen.append(n)
        return n

    def from_thread(n):
        return runtime.spawn_threadsafe(write(n), role="progress", loop=loop, owner=owner).result(timeout=5)

    assert await asyncio.to_thread(from_thread, 1) == 1
    owner.close_admission()
    owner.close_event_admission()
    with pytest.raises(OwnerClosed):
        await asyncio.to_thread(from_thread, 2)
    assert seen == [1]


async def test_the_test_fixture_binds_an_owner_that_settles_the_tests_work(db_engine):
    owner = runtime.current_owner()
    assert owner is not None and owner.name == "test"
