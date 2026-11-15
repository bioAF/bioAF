"""The installation metrics sender's owned lifecycle (ADR-075, ADR-071).

The sender is one periodic job of the application's TaskOwner, started after the schema gate with the
other workers: it pauses with them during a restore review and stops with them at shutdown. A startup
step applies a change of the operator override before any report could be sent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app import runtime, startup
from app.config import settings
from app.models.install_metric_event import InstallMetricEvent
from app.services.install_metrics import sender
from app.services.install_metrics.state import NOTICE_VERSION, InstallMetricsService
from app.tasks import jobs
from app.tasks.periodic import run_periodic

NOW = datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc)


def _job():
    return next(j for j in jobs.PERIODIC_JOBS if j.role == "install-metrics-sender")


def test_the_sender_is_an_owned_periodic_job_started_after_the_listener():
    job = _job()

    assert (job.first_delay, job.interval) == (jobs.MINUTE, 5 * jobs.MINUTE)
    assert job.error_message == "Installation metrics sender error: %s"
    # The last job is the idempotency cleanup the lifespan starts on its own (app/main.py).
    assert jobs.PERIODIC_JOBS[-1].role == "idempotency-cleanup"


def test_a_startup_step_after_the_schema_gate_applies_the_operator_override():
    names = [s.name for s in startup.NORMAL_STEPS]

    assert "install_metrics" in names
    step = next(s for s in startup.NORMAL_STEPS if s.name == "install_metrics")
    assert step.policy == "optional"


@pytest.mark.asyncio
async def test_the_startup_step_cancels_pending_reports_when_the_override_was_turned_on(
    session, admin_user, monkeypatch
):
    monkeypatch.setattr(settings, "install_metrics_disabled", False)
    await InstallMetricsService.choose(session, choice="enabled", notice_version=NOTICE_VERSION, revision=0, now=NOW)
    await session.commit()
    monkeypatch.setattr(settings, "install_metrics_disabled", True)  # the operator set it and restarted

    step = next(s for s in startup.NORMAL_STEPS if s.name == "install_metrics")
    await step.run(None)  # the step uses no startup context

    session.expire_all()
    (row,) = (await session.execute(select(InstallMetricEvent))).scalars().all()
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_one_iteration_runs_a_delivery_pass(session, admin_user, monkeypatch):
    calls = []

    async def fake_pass(factory, **kwargs):
        calls.append(factory)
        return {}

    monkeypatch.setattr(sender, "send_pending", fake_pass)

    await _job().prepare()()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_paused_owner_skips_delivery_and_a_stop_ends_the_job(monkeypatch):
    calls = []

    async def fake_pass(factory, **kwargs):
        calls.append(1)
        return {}

    monkeypatch.setattr(sender, "send_pending", fake_pass)
    owner = runtime.TaskOwner("test")
    owner._paused = "restore review"

    async def no_wait(delay, stopping):
        await asyncio.sleep(0)
        return len(calls) > 0 or stopping.is_set()

    stopping = asyncio.Event()
    task = owner.spawn(
        run_periodic(_job(), stopping=stopping, wait=no_wait), role="install-metrics-sender", worker=True
    )
    await asyncio.sleep(0.01)
    assert calls == []
    stopping.set()
    await asyncio.wait_for(task, 1)


def test_the_sender_request_is_bounded_by_a_short_timeout():
    assert sender.TIMEOUT_SECONDS == 3.0
    assert sender.BATCH == 5
    assert isinstance(sender.transport_factory(), httpx.MockTransport)  # the suite's guard
