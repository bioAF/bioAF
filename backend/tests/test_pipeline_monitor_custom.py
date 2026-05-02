"""Tests for pipeline monitor handling of custom pipeline runs.

Custom pipelines (PipelineRun.custom_pipeline_version_id is not None)
share the K8s polling path with Nextflow but skip trace parsing,
detect their own report artifacts, and may serve a user-provided
log file in place of pod stdout/stderr.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.pipeline_process import PipelineProcess
from app.models.pipeline_run import PipelineRun
from app.services.pipeline_monitor_service import (
    PipelineMonitorService,
    _find_custom_log,
    _find_custom_report,
)


@pytest_asyncio.fixture
async def env_version(session, admin_user):
    env = Environment(
        name="Custom Monitor Env",
        organization_id=admin_user.organization_id,
        created_by_user_id=admin_user.id,
        environment_type="pipeline",
    )
    session.add(env)
    await session.flush()
    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="conda",
        definition_content="name: env\n",
        image_uri="us-central1-docker.pkg.dev/test/bioaf/env:v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


async def _make_custom_run(
    session,
    admin_user,
    env_version,
    *,
    log_file_path: str | None = None,
):
    suffix = (log_file_path or "no-log").replace("/", "_")
    pipeline = CustomPipeline(
        organization_id=admin_user.organization_id,
        name=f"Custom Pipeline {suffix}",
        pipeline_key=f"custom-pipeline-{suffix}-{admin_user.id}",
        created_by_user_id=admin_user.id,
    )
    session.add(pipeline)
    await session.flush()

    version = CustomPipelineVersion(
        custom_pipeline_id=pipeline.id,
        version_number=1,
        code_source_type="inline",
        code_content="echo hello",
        entrypoint_command="bash /code/script.sh",
        environment_version_id=env_version.id,
        cpu_request="2",
        memory_request="4Gi",
        log_file_path=log_file_path,
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name=f"Custom Monitor Exp {log_file_path or 'no-log'}",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name=pipeline.name,
        pipeline_version="1",
        status="running",
        k8s_job_name=f"bioaf-custom-{pipeline.id}",
        k8s_namespace="bioaf-pipelines",
        custom_pipeline_version_id=version.id,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run, version


# --- Helper unit tests ---


def test_find_custom_report_prefers_html():
    collected = [
        {"gcs_uri": "gs://b/exp/1/runs/2/report/report.md"},
        {"gcs_uri": "gs://b/exp/1/runs/2/report/report.html"},
        {"gcs_uri": "gs://b/exp/1/runs/2/data.csv"},
    ]
    uri, fmt = _find_custom_report(collected)
    assert fmt == "html"
    assert uri.endswith("/report/report.html")


def test_find_custom_report_falls_back_to_md():
    collected = [
        {"gcs_uri": "gs://b/exp/1/runs/2/report/report.md"},
        {"gcs_uri": "gs://b/exp/1/runs/2/data.csv"},
    ]
    uri, fmt = _find_custom_report(collected)
    assert fmt == "md"
    assert uri.endswith("/report/report.md")


def test_find_custom_report_none_when_missing():
    collected = [{"gcs_uri": "gs://b/exp/1/runs/2/data.csv"}]
    assert _find_custom_report(collected) == (None, None)


def test_find_custom_log_matches_outputs_relative_path():
    collected = [
        {"gcs_uri": "gs://b/exp/1/runs/2/analysis.log"},
        {"gcs_uri": "gs://b/exp/1/runs/2/data.csv"},
    ]
    assert _find_custom_log(collected, "/outputs/analysis.log") == ("gs://b/exp/1/runs/2/analysis.log")


def test_find_custom_log_supports_nested_path():
    collected = [
        {"gcs_uri": "gs://b/exp/1/runs/2/logs/run.log"},
    ]
    assert _find_custom_log(collected, "/outputs/logs/run.log") == ("gs://b/exp/1/runs/2/logs/run.log")


def test_find_custom_log_returns_none_when_missing():
    collected = [{"gcs_uri": "gs://b/exp/1/runs/2/data.csv"}]
    assert _find_custom_log(collected, "/outputs/analysis.log") is None


# --- Completion behavior tests ---


@pytest.mark.asyncio
async def test_custom_completion_sets_simple_progress(session, admin_user, env_version):
    """Custom pipelines skip Nextflow trace parsing and use simple 1-process progress."""
    run, _ = await _make_custom_run(session, admin_user, env_version)

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "pod-xyz",
    }
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    # Custom pipelines have no trace; get_job_progress must NOT be called.
    mock_compute.get_job_progress.assert_not_called()

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert refreshed.status == "completed"
    assert refreshed.progress_json == {
        "total_processes": 1,
        "completed": 1,
        "running": 0,
        "failed": 0,
        "cached": 0,
        "percent_complete": 100.0,
    }


@pytest.mark.asyncio
async def test_custom_failure_sets_simple_progress(session, admin_user, env_version):
    run, _ = await _make_custom_run(session, admin_user, env_version)

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "failed",
        "pod_name": "pod-xyz",
        "termination_reasons": [],
    }
    mock_compute.get_job_logs.return_value = "boom"
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    mock_compute.get_job_progress.assert_not_called()

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert refreshed.status == "failed"
    assert refreshed.progress_json["failed"] == 1
    assert refreshed.progress_json["completed"] == 0
    assert refreshed.failure_reason == "task_error"


@pytest.mark.asyncio
async def test_custom_completion_skips_nextflow_metadata(session, admin_user, env_version):
    """Custom pipelines must not call register_nextflow_metadata (no report.html in raw bucket)."""
    run, _ = await _make_custom_run(session, admin_user, env_version)

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "pod-xyz",
    }
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch(
            "app.services.pipeline_output_service.PipelineOutputService.register_nextflow_metadata",
            new_callable=AsyncMock,
        ) as mock_register_nf,
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    mock_register_nf.assert_not_called()


@pytest.mark.asyncio
async def test_custom_completion_detects_html_report(session, admin_user, env_version):
    run, _ = await _make_custom_run(session, admin_user, env_version)
    exp_id = run.experiment_id

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "completed", "pod_name": "pod"}
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "report.html",
            "gcs_uri": f"gs://results/experiments/{exp_id}/pipeline-runs/{run.id}/report/report.html",
            "size_bytes": 1024,
            "md5_hash": "x",
        },
        {
            "filename": "data.csv",
            "gcs_uri": f"gs://results/experiments/{exp_id}/pipeline-runs/{run.id}/data.csv",
            "size_bytes": 50,
            "md5_hash": "y",
        },
    ]

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert refreshed.output_files_json["report_format"] == "html"
    assert refreshed.output_files_json["report_path"].endswith("/report/report.html")


@pytest.mark.asyncio
async def test_custom_completion_detects_md_report_when_no_html(session, admin_user, env_version):
    run, _ = await _make_custom_run(session, admin_user, env_version)
    exp_id = run.experiment_id

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "completed", "pod_name": "pod"}
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "report.md",
            "gcs_uri": f"gs://results/experiments/{exp_id}/pipeline-runs/{run.id}/report/report.md",
            "size_bytes": 256,
            "md5_hash": "x",
        },
    ]

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert refreshed.output_files_json["report_format"] == "md"
    assert refreshed.output_files_json["report_path"].endswith("/report/report.md")


@pytest.mark.asyncio
async def test_custom_completion_no_report_no_metadata(session, admin_user, env_version):
    run, _ = await _make_custom_run(session, admin_user, env_version)
    exp_id = run.experiment_id

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "completed", "pod_name": "pod"}
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "data.csv",
            "gcs_uri": f"gs://results/experiments/{exp_id}/pipeline-runs/{run.id}/data.csv",
            "size_bytes": 50,
            "md5_hash": "y",
        },
    ]

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert "report_path" not in refreshed.output_files_json
    assert "report_format" not in refreshed.output_files_json


@pytest.mark.asyncio
async def test_custom_completion_records_custom_log_path(session, admin_user, env_version):
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path="/outputs/analysis.log")
    exp_id = run.experiment_id

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "completed", "pod_name": "pod"}
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "analysis.log",
            "gcs_uri": f"gs://results/experiments/{exp_id}/pipeline-runs/{run.id}/analysis.log",
            "size_bytes": 100,
            "md5_hash": "z",
        },
    ]

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    refreshed = (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()
    assert refreshed.output_files_json["custom_log_path"].endswith("/analysis.log")


@pytest.mark.asyncio
async def test_custom_completion_does_not_create_process_records(session, admin_user, env_version):
    """Custom pipelines have one wrapper process; we should not create per-step PipelineProcess rows."""
    run, _ = await _make_custom_run(session, admin_user, env_version)

    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "completed", "pod_name": "pod"}
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    rows = (
        (await session.execute(select(PipelineProcess).where(PipelineProcess.pipeline_run_id == run.id)))
        .scalars()
        .all()
    )
    assert list(rows) == []


# --- Log retrieval tests ---


@pytest.mark.asyncio
async def test_get_run_logs_no_log_file_returns_pod_logs(session, admin_user, env_version):
    """Custom pipeline with no log_file_path: returns pod logs, no special metadata."""
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path=None)

    mock_compute = AsyncMock()
    mock_compute.get_job_logs.return_value = "pod stdout content"

    with patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute):
        result = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert result["stdout"] == "pod stdout content"
    assert result["stderr"] == ""
    assert "log_source" not in result
    assert "custom_log_pending" not in result
    assert "pod_logs_available" not in result


@pytest.mark.asyncio
async def test_get_run_logs_running_returns_pod_with_pending_flag(session, admin_user, env_version):
    """Custom pipeline with log_file_path while running: pod logs + custom_log_pending=true."""
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path="/outputs/analysis.log")
    # status is already "running" from fixture

    mock_compute = AsyncMock()
    mock_compute.get_job_logs.return_value = "live pod stdout"

    with patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute):
        result = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert result["stdout"] == "live pod stdout"
    assert result["log_source"] == "pod"
    assert result["custom_log_pending"] is True


@pytest.mark.asyncio
async def test_get_run_logs_completed_returns_custom_log_file(session, admin_user, env_version):
    """After completion with log_file_path set, returns custom log content + pod_logs_available=true."""
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path="/outputs/analysis.log")
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.output_files_json = {
        "files": ["analysis.log"],
        "custom_log_path": "gs://results/exp/1/runs/2/analysis.log",
    }
    await session.commit()

    with patch(
        "app.services.pipeline_monitor_service._read_gcs_text",
        new=AsyncMock(return_value="custom log file body"),
    ):
        result = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert result["stdout"] == "custom log file body"
    assert result["log_source"] == "custom_file"
    assert result["pod_logs_available"] is True


@pytest.mark.asyncio
async def test_get_run_logs_completed_missing_custom_log_falls_back(session, admin_user, env_version):
    """If the custom log file is missing in GCS, fall back to pod logs with custom_log_missing=true."""
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path="/outputs/analysis.log")
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    # No custom_log_path stored - simulates collect_outputs not finding the log
    run.output_files_json = {"files": []}
    await session.commit()

    mock_compute = AsyncMock()
    mock_compute.get_job_logs.return_value = "pod fallback stdout"

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
    ):
        result = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert result["stdout"] == "pod fallback stdout"
    assert result["log_source"] == "pod"
    assert result["custom_log_missing"] is True


@pytest.mark.asyncio
async def test_get_run_logs_completed_gcs_read_failure_falls_back(session, admin_user, env_version):
    """If reading the GCS log fails, fall back to pod logs with custom_log_missing=true."""
    run, _ = await _make_custom_run(session, admin_user, env_version, log_file_path="/outputs/analysis.log")
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.output_files_json = {
        "files": ["analysis.log"],
        "custom_log_path": "gs://results/exp/1/runs/2/analysis.log",
    }
    await session.commit()

    mock_compute = AsyncMock()
    mock_compute.get_job_logs.return_value = "pod fallback stdout"

    with (
        patch(
            "app.services.pipeline_monitor_service._read_gcs_text",
            new=AsyncMock(return_value=None),
        ),
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
    ):
        result = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert result["stdout"] == "pod fallback stdout"
    assert result["log_source"] == "pod"
    assert result["custom_log_missing"] is True
