"""Tests for reaping the work directories of abandoned pipeline runs.

`gs://bioaf-raw-.../nextflow-work` held **2.13 TB** after 5 runs, and nothing
deleted any of it. Nextflow's work dir holds every task's intermediates: decompressed
references, STAR indexes, BAMs. For a run that failed it is pure garbage the moment
the retry window closes, and it is billed monthly forever.

Two halves:

1. The work dir must be scoped per run, or a failed run's data cannot be told from a
   healthy one's and none of it can be deleted safely.
2. A reaper deletes it two days after the run reached a terminal unsuccessful state:
   long enough to retry or diagnose, short enough not to accumulate.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.models.pipeline_run import PipelineRun
from app.services.work_dir_reaper import (
    WORK_DIR_RETENTION_DAYS,
    WorkDirReaper,
    work_dir_key,
)


# -- half one: the work dir is per run ---------------------------------------


def test_the_work_dir_key_is_scoped_to_its_run():
    """A single shared directory cannot be cleaned: one run's garbage is
    indistinguishable from another's live intermediates."""
    assert work_dir_key(45) == "nextflow-work/run-45"
    assert work_dir_key(45) != work_dir_key(46)


def test_the_launcher_and_the_reaper_agree_on_the_location():
    """If these ever diverge the reaper silently deletes nothing, which looks
    exactly like having nothing to delete."""
    assert KubernetesComputeProvider.scratch_work_dir_key(45) == work_dir_key(45)


# -- half two: the reaper ----------------------------------------------------


@pytest_asyncio.fixture
async def terminal_run(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Reaper Test Experiment",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    def _make(status: str, days_ago: float) -> PipelineRun:
        run = PipelineRun(
            organization_id=admin_user.organization_id,
            experiment_id=exp.id,
            submitted_by_user_id=admin_user.id,
            pipeline_name="nf-core/scrnaseq",
            status=status,
            compute_job_ref="bioaf-pipeline-x",
            completed_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
        session.add(run)
        return run

    return _make


def _storage():
    adapter = MagicMock()
    adapter.list_objects = AsyncMock(
        return_value=[MagicMock(uri="gs://b/nextflow-work/run-1/a"), MagicMock(uri="gs://b/nextflow-work/run-1/b")]
    )
    adapter.delete = AsyncMock()
    adapter.build_uri = MagicMock(side_effect=lambda bucket, key: f"gs://{bucket}/{key}")
    return adapter


class TestReaper:
    @pytest.mark.asyncio
    async def test_a_failed_run_past_the_window_has_its_work_dir_deleted(self, session, terminal_run):
        run = terminal_run("failed", WORK_DIR_RETENTION_DAYS + 1)
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id in reaped
        assert storage.delete.await_count == 2

    @pytest.mark.asyncio
    async def test_a_run_inside_the_retry_window_is_left_alone(self, session, terminal_run):
        """Two days exists so a retry or a diagnosis can still use the data."""
        run = terminal_run("failed", WORK_DIR_RETENTION_DAYS - 0.5)
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id not in reaped
        storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_cancelled_run_is_also_abandoned(self, session, terminal_run):
        run = terminal_run("cancelled", WORK_DIR_RETENTION_DAYS + 1)
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id in reaped

    @pytest.mark.asyncio
    async def test_a_completed_run_is_never_reaped_by_this(self, session, terminal_run):
        """Out of scope deliberately: a successful run's outputs are published from
        its work dir, and deleting them is a different decision."""
        run = terminal_run("completed", WORK_DIR_RETENTION_DAYS + 30)
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id not in reaped

    @pytest.mark.asyncio
    async def test_a_running_run_is_never_reaped(self, session, terminal_run):
        """It has no completed_at and its intermediates are in active use."""
        run = terminal_run("running", 0)
        run.completed_at = None
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id not in reaped

    @pytest.mark.asyncio
    async def test_a_run_is_not_reaped_twice(self, session, terminal_run):
        """The scan would otherwise re-list and re-delete an empty prefix forever."""
        run = terminal_run("failed", WORK_DIR_RETENTION_DAYS + 1)
        await session.flush()
        storage = _storage()

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")
            storage.delete.reset_mock()
            second = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id not in second
        storage.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_storage_failure_does_not_mark_the_run_reaped(self, session, terminal_run):
        """Otherwise a transient error silently strands the data forever."""
        run = terminal_run("failed", WORK_DIR_RETENTION_DAYS + 1)
        await session.flush()
        storage = _storage()
        storage.delete = AsyncMock(side_effect=RuntimeError("GCS unavailable"))

        with patch("app.services.work_dir_reaper.get_storage_adapter", return_value=storage):
            reaped = await WorkDirReaper.reap(session, raw_bucket="bioaf-raw-test")

        assert run.id not in reaped
        assert not (run.provider_metadata or {}).get("work_dir_reaped")
