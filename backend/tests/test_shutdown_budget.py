"""The lifespan's shutdown limits are enforced, not advisory.

Every phase draws on one budget: the listener stop is part of the drain and cancel phases, not an
extra allowance, and a step that suppresses cancellation is abandoned at its deadline rather than
awaited past it. uvicorn's request grace plus the lifespan's phases fit inside the container's stop
grace.
"""

import asyncio
import re
from pathlib import Path

import pytest
import pytest_asyncio
import yaml

from app import runtime, startup
from app.runtime import TaskOwner

pytestmark = pytest.mark.asyncio

DOCKER = Path(__file__).resolve().parent.parent.parent / "docker"


_release = asyncio.Event()


async def _stubborn():
    """Ignores cancellation until the test releases it (so the test's loop can still close)."""
    while not _release.is_set():
        try:
            await _release.wait()
        except asyncio.CancelledError:
            continue


@pytest_asyncio.fixture(autouse=True)
async def _release_stubborn_work():
    global _release
    _release = asyncio.Event()
    yield
    _release.set()
    await asyncio.sleep(0.01)


async def test_a_step_that_suppresses_cancellation_is_abandoned_at_its_deadline():
    loop = asyncio.get_running_loop()
    started = loop.time()
    finished = await startup.bounded(_stubborn(), 0.2, "probe")
    assert not finished
    assert loop.time() - started < 0.5


async def test_the_whole_shutdown_fits_one_budget(monkeypatch):
    from app import main
    from app.services import pubsub_listener

    monkeypatch.setattr(startup, "DRAIN_SECONDS", 0.2)
    monkeypatch.setattr(startup, "CANCEL_SECONDS", 0.3)
    monkeypatch.setattr(startup, "RESOURCE_SECONDS", 0.2)
    owner = TaskOwner()
    manager = pubsub_listener.ListenerManager()
    with runtime.bound(owner):
        manager._listener = pubsub_listener.PubSubListener()
        manager._task = owner.spawn(_stubborn(), role="pubsub-listener", worker=True)
        owner.spawn(_stubborn(), role="stubborn-job")
    monkeypatch.setattr(pubsub_listener, "_manager", manager)

    class Engine:
        async def dispose(self):
            await _stubborn()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await main._stop_owned_work(owner)
    await main._dispose_engine(Engine(), owner)
    elapsed = loop.time() - started

    assert elapsed < 0.2 + 0.3 + 0.2 + 0.3
    assert owner.report is not None and not owner.report.clean


async def test_a_clean_shutdown_still_bounds_engine_disposal(monkeypatch):
    from app import main

    monkeypatch.setattr(startup, "RESOURCE_SECONDS", 0.2)
    owner = TaskOwner()
    await owner.shutdown(drain_seconds=0, cancel_seconds=0)

    class Engine:
        async def dispose(self):
            await _stubborn()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await main._dispose_engine(Engine(), owner)
    assert loop.time() - started < 0.5


def test_request_grace_and_lifespan_phases_fit_the_container_stop_grace():
    dockerfile = (DOCKER / "Dockerfile.backend").read_text()
    request_grace = int(re.search(r'"--timeout-graceful-shutdown", "(\d+)"', dockerfile).group(1))
    backend = yaml.safe_load((DOCKER / "docker-compose.yml").read_text())["services"]["backend"]
    stop_grace = int(backend["stop_grace_period"].rstrip("s"))
    total = request_grace + startup.DRAIN_SECONDS + startup.CANCEL_SECONDS + startup.RESOURCE_SECONDS
    assert total <= stop_grace - 10, f"{total}s of shutdown leaves under 10s margin in {stop_grace}s"


# --- admitted request work is inside the shutdown fence ----------------------


async def test_shutdown_waits_for_a_cancelled_requests_cleanup_before_reporting_clean():
    owner = TaskOwner()
    admitted, cleaned = asyncio.Event(), []

    async def deployment_request():
        runtime.require_admission("deployment")
        admitted.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)  # Terraform TERM, then the run's interruption commit
            cleaned.append(True)
            raise

    with runtime.bound(owner):
        request = asyncio.create_task(deployment_request())
    await admitted.wait()
    request.cancel()  # uvicorn cancels requests that outlive its grace, then runs the lifespan
    report = await owner.shutdown(drain_seconds=0.05, cancel_seconds=2)

    assert cleaned == [True]
    assert report.clean


async def test_request_cleanup_that_overruns_the_budget_makes_shutdown_unclean():
    owner = TaskOwner()
    admitted = asyncio.Event()

    async def deployment_request():
        runtime.require_admission("deployment")
        admitted.set()
        await _stubborn()

    with runtime.bound(owner):
        request = asyncio.create_task(deployment_request())
    await admitted.wait()
    report = await owner.shutdown(drain_seconds=0.05, cancel_seconds=0.2)
    assert not report.clean
    assert report.uncooperative == ["operation:deployment"]
    _release.set()
    await asyncio.gather(request, return_exceptions=True)


async def test_an_unfinished_reservation_makes_shutdown_unclean():
    owner = TaskOwner()
    owner.reserve("hosted-review")
    report = await owner.shutdown(drain_seconds=0.05, cancel_seconds=0.1)
    assert not report.clean
    assert report.uncooperative == ["reservation:hosted-review"]
