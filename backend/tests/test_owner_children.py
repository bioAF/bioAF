"""Children of owned work stay owned through repeated cancellation.

A shielded cleanup, an event's subscriber calls, and a literature fan-out's source calls are
children of an owned task. Repeated cancellation interrupts the parent's wait on them, so the parent
can finish while they still run. Shutdown must keep waiting for them (a shielded cleanup is not
cancelled at all) and must not report a clean stop, or let the engine be disposed, before they end.
"""

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app import runtime
from app.runtime import TaskOwner

pytestmark = pytest.mark.asyncio

APP_DIR = Path(__file__).resolve().parent.parent / "app"


async def test_a_shielded_cleanup_finishes_before_shutdown_reports_clean():
    owner = TaskOwner()
    persisted = []
    entered = asyncio.Event()

    async def persist():
        await asyncio.sleep(0.2)
        persisted.append(True)

    async def job():
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await runtime.shielded(persist(), role="job-cleanup")
            raise

    owner.spawn(job(), role="job")
    await entered.wait()
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=2)

    assert persisted == [True]
    assert report.clean and owner.roles() == []


async def test_a_shielded_cleanup_that_overruns_the_budget_is_reported():
    owner = TaskOwner()
    entered = asyncio.Event()

    async def job():
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await runtime.shielded(asyncio.sleep(5), role="job-cleanup")
            raise

    owner.spawn(job(), role="job")
    await entered.wait()
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=0.2)
    assert not report.clean
    assert report.uncooperative == ["job-cleanup"]


async def test_a_cancelled_emission_leaves_no_subscriber_call_running():
    from app.services.event_bus import EventBus

    owner = TaskOwner()
    bus = EventBus()
    calls: list[asyncio.Task] = []
    running = asyncio.Event()

    async def subscriber(_payload):
        calls.append(asyncio.current_task())
        try:
            running.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)  # cleanup after cancellation
            raise

    bus.subscribe("probe", subscriber)
    with runtime.bound(owner):
        runtime.spawn_event(bus.emit("probe", {}), event_type="probe")
    await running.wait()
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=2)
    assert all(t.done() for t in calls)
    assert report.clean and owner.roles() == []


async def test_the_lit_review_runs_cancelled_outcome_is_persisted_before_shutdown_ends(
    session, admin_user, monkeypatch
):
    from app.models.experiment import Experiment
    from app.models.literature import LiteratureReviewRun
    from app.services.literature import lit_review_run_service as svc

    experiment = Experiment(organization_id=admin_user.organization_id, name="E")
    session.add(experiment)
    await session.flush()
    run = LiteratureReviewRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        triggered_by_user_id=admin_user.id,
        llm_provider="openai",
        llm_model="m",
    )
    session.add(run)
    await session.commit()
    run_id = run.id

    started = asyncio.Event()

    async def blocked(execution):
        started.set()
        await asyncio.Event().wait()

    real_fail = svc._fail

    async def slow_fail(execution, error, **kwargs):
        await asyncio.sleep(0.2)
        await real_fail(execution, error, **kwargs)

    monkeypatch.setattr(svc, "_do_run", blocked)
    monkeypatch.setattr(svc, "_fail", slow_fail)
    owner = TaskOwner()
    owner.spawn(svc._execute_run(run_id), role="lit-review-run")
    await started.wait()
    report = await owner.shutdown(drain_seconds=0, cancel_seconds=3)

    assert report.clean
    session.expire_all()
    status = (
        await session.execute(select(LiteratureReviewRun.status).where(LiteratureReviewRun.id == run_id))
    ).scalar()
    assert status == "failed"


def test_no_unowned_shield_or_coroutine_gather_in_the_application():
    """A shield or a gather over coroutines creates tasks the owner cannot see."""
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "runtime.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "shield" or (
                node.func.attr == "gather" and any(isinstance(a, ast.Call) for a in node.args)
            ):
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno}")
    assert offenders == []
