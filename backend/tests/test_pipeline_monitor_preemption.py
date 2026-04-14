"""Tests for preemption exhaustion detection in the pipeline monitor.

When a pipeline fails and the Nextflow trace shows processes with exit
codes 143/137/247 that have FAILED status, the monitor should set
failure_reason='preemption_exhausted' with a specific error message.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.models.pipeline_run import PipelineRun
from app.services.pipeline_monitor_service import PipelineMonitorService


TRACE_WITH_PREEMPTION = (
    "task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\t"
    "submit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar\n"
    "1\tab/123456\t100\tSTAR_GENOMEGENERATE\t-\tSTAR_GENOMEGENERATE (GRCh38)\tFAILED\t143\t"
    "2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:15\t15m\t14m\t85.2\t4.5 GB\t8.1 GB\t100\t200\n"
    "2\tcd/789012\t101\tFASTQC\t-\tFASTQC (SAMPLE_1)\tCOMPLETED\t0\t"
    "2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:05\t5m\t4m\t20.5\t500 MB\t1.2 GB\t50\t10\n"
)

TRACE_WITH_OOM_EXIT = (
    "task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\t"
    "submit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar\n"
    "1\tab/123456\t100\tSTAR_GENOMEGENERATE\t-\tSTAR_GENOMEGENERATE (GRCh38)\tFAILED\t137\t"
    "2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:15\t15m\t14m\t85.2\t4.5 GB\t8.1 GB\t100\t200\n"
)

TRACE_WITH_NORMAL_FAILURE = (
    "task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\t"
    "submit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar\n"
    "1\tab/123456\t100\tSTAR_GENOMEGENERATE\t-\tSTAR_GENOMEGENERATE (GRCh38)\tFAILED\t1\t"
    "2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:15\t15m\t14m\t85.2\t4.5 GB\t8.1 GB\t100\t200\n"
)


@pytest_asyncio.fixture
async def k8s_failed_preemption_run(session, admin_user):
    """Pipeline run that failed after Spot preemption exhausted retries."""
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Preemption Test Experiment",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        status="running",
        k8s_job_name="bioaf-pipeline-preempt-1",
        k8s_namespace="bioaf-pipelines",
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


def _make_mock_compute(termination_reasons=None, trace_content=""):
    """Build a mock compute adapter returning the given K8s status and trace."""
    mock = AsyncMock()
    status = {
        "status": "failed",
        "pod_name": "bioaf-pipeline-preempt-1-xyz",
        "node_name": "gke-node-1",
        "termination_reasons": termination_reasons or [],
    }
    mock.get_job_status.return_value = status
    mock.get_job_progress.return_value = {"percent_complete": 0.0, "processes": []}
    mock.get_job_logs.return_value = trace_content or "Pipeline failed"
    return mock


@pytest.mark.asyncio
async def test_preemption_exhaustion_detected_from_trace(session, k8s_failed_preemption_run):
    """Exit 143 with FAILED status in trace -> failure_reason='preemption_exhausted'."""
    mock_compute = _make_mock_compute(
        termination_reasons=[{"container": "pipeline", "exit_code": 143, "reason": "Error"}],
    )
    # The monitor reads the trace from get_job_progress processes or parses trace.tsv.
    # For the preemption path, OOM is checked first (no OOMKilled), then trace is checked.
    # We simulate the trace being available via the progress adapter.
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 0.0,
        "processes": [
            {"name": "STAR_GENOMEGENERATE", "status": "failed", "exit_code": 143},
            {"name": "FASTQC", "status": "completed"},
        ],
    }

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_failed_preemption_run.id))
    run = result.scalar_one()

    assert run.status == "failed"
    assert run.failure_reason == "preemption_exhausted"
    assert "Spot" in run.error_message or "interruption" in run.error_message.lower()


@pytest.mark.asyncio
async def test_preemption_exit_137_detected(session, k8s_failed_preemption_run):
    """Exit 137 with FAILED status (non-OOMKilled K8s reason) -> preemption_exhausted."""
    mock_compute = _make_mock_compute(
        termination_reasons=[{"container": "pipeline", "exit_code": 137, "reason": "Error"}],
    )
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 0.0,
        "processes": [
            {"name": "STAR_GENOMEGENERATE", "status": "failed", "exit_code": 137},
        ],
    }

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_failed_preemption_run.id))
    run = result.scalar_one()

    assert run.failure_reason == "preemption_exhausted"


@pytest.mark.asyncio
async def test_normal_failure_sets_task_error(session, k8s_failed_preemption_run):
    """A failure with exit code 1 (not preemption) -> failure_reason='task_error'."""
    mock_compute = _make_mock_compute(
        termination_reasons=[{"container": "pipeline", "exit_code": 1, "reason": "Error"}],
    )
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 0.0,
        "processes": [
            {"name": "STAR_GENOMEGENERATE", "status": "failed", "exit_code": 1},
        ],
    }

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_failed_preemption_run.id))
    run = result.scalar_one()

    assert run.failure_reason == "task_error"
