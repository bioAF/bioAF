"""The shared periodic scheduler.

Each job keeps its timing (first run after its first delay, the next delay counted from when the
previous iteration settled, never overlapping), its own error message, and its transaction
boundaries. The scheduler owns waiting, stopping, and cancellation; a failed iteration is reported
through the job's message and the next one runs on the existing interval.
"""

import asyncio
import logging

import pytest

from app.tasks import jobs
from app.tasks.periodic import PeriodicJob, run_periodic

pytestmark = pytest.mark.asyncio


class FakeWait:
    """Records each requested delay; returns at once, or reports a stop after ``stop_after`` waits."""

    def __init__(self, stop_after: int):
        self.delays: list[float] = []
        self.stop_after = stop_after

    async def __call__(self, delay: float, stopping) -> bool:
        self.delays.append(delay)
        await asyncio.sleep(0)
        return len(self.delays) > self.stop_after


def _job(iteration, *, first=5.0, interval=7.0, message="Thing error: %s", enabled=None):
    return PeriodicJob(
        role="thing",
        prepare=lambda: iteration,
        interval=interval,
        first_delay=first,
        error_message=message,
        enabled=enabled,
    )


async def test_first_run_waits_its_first_delay_then_the_interval():
    ran = []

    async def iteration():
        ran.append(True)

    wait = FakeWait(stop_after=3)
    await run_periodic(_job(iteration), wait=wait)
    assert wait.delays == [5.0, 7.0, 7.0, 7.0]
    assert len(ran) == 3


async def test_a_slow_iteration_is_not_overlapped_and_the_delay_starts_after_it():
    events = []
    release = asyncio.Event()

    async def iteration():
        events.append("start")
        await release.wait()
        events.append("end")

    class Wait(FakeWait):
        async def __call__(self, delay, stopping):
            events.append(("wait", delay))
            return await super().__call__(delay, stopping)

    runner = asyncio.create_task(run_periodic(_job(iteration), wait=Wait(stop_after=2)))
    await asyncio.sleep(0.01)
    assert events == [("wait", 5.0), "start"]
    release.set()
    await runner
    assert events == [("wait", 5.0), "start", "end", ("wait", 7.0), "start", "end", ("wait", 7.0)]


async def test_a_failure_uses_the_jobs_message_and_the_next_iteration_still_runs(caplog):
    calls = []

    async def iteration():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("bucket missing")

    with caplog.at_level(logging.ERROR, logger="bioaf"):
        await run_periodic(_job(iteration), wait=FakeWait(stop_after=2))
    assert len(calls) == 2
    domain = [r for r in caplog.records if r.name == "bioaf" and r.getMessage() == "Thing error: bucket missing"]
    assert len(domain) == 1


async def test_a_disabled_job_never_runs():
    ran = []

    async def iteration():
        ran.append(True)

    wait = FakeWait(stop_after=5)
    await run_periodic(_job(iteration, enabled=lambda: False), wait=wait)
    assert ran == [] and wait.delays == []


async def test_a_stop_while_waiting_ends_the_job_without_another_iteration():
    ran = []

    async def iteration():
        ran.append(True)

    stopping = asyncio.Event()
    runner = asyncio.create_task(run_periodic(_job(iteration, first=3600), stopping=stopping))
    await asyncio.sleep(0.01)
    stopping.set()
    await asyncio.wait_for(runner, 1)
    assert ran == []


async def test_a_stop_during_an_iteration_lets_it_finish_first():
    finished = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def iteration():
        started.set()
        await release.wait()
        finished.append(True)

    stopping = asyncio.Event()
    runner = asyncio.create_task(run_periodic(_job(iteration, first=0), stopping=stopping))
    await started.wait()
    stopping.set()
    release.set()
    await asyncio.wait_for(runner, 1)
    assert finished == [True]


async def test_cancellation_propagates():
    async def iteration():
        await asyncio.Event().wait()

    runner = asyncio.create_task(run_periodic(_job(iteration, first=0)))
    await asyncio.sleep(0.01)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner


async def test_prepare_runs_once_per_job_lifetime():
    prepared = []

    def prepare():
        prepared.append(True)
        state = {"n": 0}

        async def iteration():
            state["n"] += 1

        return iteration

    job = PeriodicJob(role="x", prepare=prepare, interval=1, first_delay=1, error_message="x: %s")
    await run_periodic(job, wait=FakeWait(stop_after=3))
    assert prepared == [True]


# The timing contract of every job.
TIMINGS = {
    "pipeline-monitor": (30, 30),
    "auto-run-launch": (30, 30),
    "validation-driver": (30, 30),
    "notebook-image-build": (30, 30),
    "cellxgene-image-build": (30, 30),
    "environment-build-poll": (30, 30),
    "job-status-sync": (60, 60),
    "plot-archive-watcher": (60, 60),
    "postgres-backup": (60, 60),
    "config-backup": (60, 60),
    "lit-review-auto": (60, 60),
    "session-monitor": (60, 60),
    "work-node-heartbeat": (60, 60),
    "idle-session-check": (300, 300),
    "quota-reset": (3600, 3600),
    "storage-stats-refresh": (3600, 3600),
    "backup-health-check": (3600, 3600),
    "export-cleanup": (3600, 3600),
    "idempotency-cleanup": (3600, 3600),
    "notification-cleanup": (86400, 86400),
    "work-dir-reaper": (86400, 86400),
    "validation-fetch-reaper": (86400, 86400),
    "version-check": (86400, 86400),
    "sdr-trigger": (86400, 86400),
    "review-reminder": (21600, 21600),
    "cost-billing-sync": (60, 86400),
    "nf-core-registry-refresh": (60, 86400),
}

# Each job's existing error message (the logger.error lines its loop had in app/main.py).
MESSAGES = {
    "job-status-sync": "Job status sync error: %s",
    "idle-session-check": "Idle session check error: %s",
    "quota-reset": "Quota reset error: %s",
    "pipeline-monitor": "Pipeline monitor error: %s",
    "plot-archive-watcher": "Plot archive watcher error: %s",
    "storage-stats-refresh": "Storage stats refresh error: %s",
    "notification-cleanup": "Notification cleanup error: %s",
    "work-dir-reaper": "Work dir reaper error: %s",
    "validation-fetch-reaper": "Validation fetch reaper error: %s",
    "idempotency-cleanup": "Idempotency cleanup error: %s",
    "backup-health-check": "Backup health check error: %s",
    "postgres-backup": "Postgres backup loop error: %s",
    "config-backup": "Config backup loop error: %s",
    "lit-review-auto": "Lit review auto loop error: %s",
    "cost-billing-sync": "Cost billing sync error: %s",
    "version-check": "Version check error: %s",
    "review-reminder": "Review reminder error: %s",
    "sdr-trigger": "SDR trigger loop error: %s",
    "auto-run-launch": "Auto-run launch loop error: %s",
    "validation-driver": "Validation driver loop error: %s",
    "session-monitor": "Session monitor error: %s",
    "notebook-image-build": "Notebook image build monitor error: %s",
    "cellxgene-image-build": "Cellxgene image build monitor error: %s",
    "environment-build-poll": "Environment build poll error: %s",
    "work-node-heartbeat": "Work node heartbeat check error: %s",
    "export-cleanup": "Export cleanup error: %s",
    "nf-core-registry-refresh": "nf-core registry refresh loop error: %s",
}


def test_every_job_keeps_its_timing_and_its_error_message():
    by_role = {job.role: job for job in jobs.PERIODIC_JOBS}
    assert {r: (j.first_delay, j.interval) for r, j in by_role.items()} == TIMINGS
    assert {r: j.error_message for r, j in by_role.items()} == MESSAGES
    assert len(jobs.PERIODIC_JOBS) == 27


async def test_the_plot_archive_backfill_runs_once_per_job_lifetime(monkeypatch, session):
    from app.services.plot_archive_service import PlotArchiveService

    backfills, scans = [], []

    async def backfill(s):
        backfills.append(True)

    async def scan(s):
        scans.append(True)

    monkeypatch.setattr(PlotArchiveService, "backfill_metadata", staticmethod(backfill))
    monkeypatch.setattr(PlotArchiveService, "scan_and_index", staticmethod(scan))
    job = next(j for j in jobs.PERIODIC_JOBS if j.role == "plot-archive-watcher")
    await run_periodic(job, wait=FakeWait(stop_after=3))
    assert (len(backfills), len(scans)) == (1, 3)


async def test_slurm_job_sync_is_disabled_outside_slurm_mode(monkeypatch):
    from app.config import settings

    job = next(j for j in jobs.PERIODIC_JOBS if j.role == "job-status-sync")
    enabled = job.enabled
    assert enabled is not None
    monkeypatch.setattr(settings, "compute_mode", "kubernetes")
    assert not enabled()
    monkeypatch.setattr(settings, "compute_mode", "slurm")
    assert enabled()
