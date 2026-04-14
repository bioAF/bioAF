"""Tests for OOM detection in the pipeline monitor.

When a K8s job fails and a container's terminated reason is OOMKilled,
the monitor should set failure_reason='oom', write a specific error
message, and emit a PIPELINE_OOM event.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.models.pipeline_run import PipelineRun
from app.services.pipeline_monitor_service import PipelineMonitorService


@pytest_asyncio.fixture
async def k8s_failed_oom_run(session, admin_user):
    """Pipeline run whose K8s job will report OOMKilled termination."""
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="OOM Test Experiment",
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
        k8s_job_name="bioaf-pipeline-oom-1",
        k8s_namespace="bioaf-pipelines",
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_oom_detection_sets_failure_reason(session, k8s_failed_oom_run):
    """When K8s reports OOMKilled, failure_reason should be 'oom'."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "failed",
        "pod_name": "bioaf-pipeline-oom-1-abc",
        "node_name": "gke-node-1",
        "termination_reasons": [
            {
                "container": "pipeline",
                "exit_code": 137,
                "reason": "OOMKilled",
            }
        ],
    }
    mock_compute.get_job_progress.return_value = {"percent_complete": 0.0, "processes": []}
    mock_compute.get_job_logs.return_value = "STAR genome generate failed"

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_failed_oom_run.id))
    run = result.scalar_one()

    assert run.status == "failed"
    assert run.failure_reason == "oom"
    assert "out of memory" in run.error_message.lower()
    assert "Infrastructure" in run.error_message


@pytest.mark.asyncio
async def test_oom_detection_emits_pipeline_oom_event(session, k8s_failed_oom_run):
    """OOM failure should emit a PIPELINE_OOM event through the event bus."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "failed",
        "pod_name": "bioaf-pipeline-oom-1-abc",
        "node_name": "gke-node-1",
        "termination_reasons": [
            {
                "container": "pipeline",
                "exit_code": 137,
                "reason": "OOMKilled",
            }
        ],
    }
    mock_compute.get_job_progress.return_value = {"percent_complete": 0.0, "processes": []}
    mock_compute.get_job_logs.return_value = "OOM killed"

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    emitted_events = []

    async def capture_emit(event_type, payload):
        emitted_events.append((event_type, payload))

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
        patch("app.services.pipeline_monitor_service.event_bus.emit", side_effect=capture_emit),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from app.services.event_types import PIPELINE_OOM

    oom_events = [(t, p) for t, p in emitted_events if t == PIPELINE_OOM]
    assert len(oom_events) == 1
    payload = oom_events[0][1]
    assert payload["run_id"] == k8s_failed_oom_run.id
    assert payload["pipeline_name"] == "nf-core/scrnaseq"
    assert "severity" in payload


@pytest.mark.asyncio
async def test_non_oom_failure_does_not_set_oom_reason(session, k8s_failed_oom_run):
    """A regular task failure (no OOMKilled) should not set failure_reason='oom'."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "failed",
        "pod_name": "bioaf-pipeline-oom-1-abc",
        "node_name": "gke-node-1",
        "termination_reasons": [
            {
                "container": "pipeline",
                "exit_code": 1,
                "reason": "Error",
            }
        ],
    }
    mock_compute.get_job_progress.return_value = {"percent_complete": 0.0, "processes": []}
    mock_compute.get_job_logs.return_value = "Process exited with error"

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_failed_oom_run.id))
    run = result.scalar_one()

    assert run.status == "failed"
    assert run.failure_reason == "task_error"
