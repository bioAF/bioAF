import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.models.pipeline_run import PipelineRun
from app.services.pipeline_monitor_service import PipelineMonitorService, _parse_memory_gb, _parse_duration


# --- Unit tests for trace file parsing ---

SAMPLE_TRACE_TSV = """task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\tsubmit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar
1\tab/123456\t100\tSTARSOLO\t-\tSTARSOLO (SAMPLE_1)\tCOMPLETED\t0\t2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:30\t30m\t29m 45s\t85.2\t4.5 GB\t8.1 GB\t100\t200
2\tcd/789012\t101\tSAMTOOLS_SORT\t-\tSAMTOOLS_SORT (SAMPLE_1)\tRUNNING\t-\t2026-01-01 00:30\t2026-01-01 00:31\t-\t-\t-\t-\t-\t-\t-\t-
3\tef/345678\t102\tFASTQC\t-\tFASTQC (SAMPLE_1)\tFAILED\t1\t2026-01-01 00:00\t2026-01-01 00:01\t2026-01-01 00:05\t5m\t4m 30s\t20.5\t500 MB\t1.2 GB\t50\t10
"""


def test_parse_trace_tsv():
    """Parse a Nextflow trace.tsv into process records."""
    processes = PipelineMonitorService.parse_trace_tsv(SAMPLE_TRACE_TSV)
    assert len(processes) == 3
    assert processes[0]["process"] == "STARSOLO"
    assert processes[0]["status"] == "COMPLETED"
    assert processes[0]["exit"] == "0"
    assert processes[1]["status"] == "RUNNING"
    assert processes[2]["status"] == "FAILED"


def test_map_nf_status():
    """Map Nextflow status strings to our status."""
    assert PipelineMonitorService._map_nf_status("COMPLETED") == "completed"
    assert PipelineMonitorService._map_nf_status("RUNNING") == "running"
    assert PipelineMonitorService._map_nf_status("FAILED") == "failed"
    assert PipelineMonitorService._map_nf_status("CACHED") == "cached"
    assert PipelineMonitorService._map_nf_status("SUBMITTED") == "pending"


def test_parse_memory_gb():
    """Parse memory values from trace."""
    assert _parse_memory_gb("4.5 GB") == 4.5
    assert _parse_memory_gb("500 MB") == pytest.approx(0.49, rel=0.1)
    assert _parse_memory_gb("-") is None
    assert _parse_memory_gb(None) is None
    assert _parse_memory_gb("") is None


def test_parse_duration():
    """Parse duration values from trace."""
    assert _parse_duration("30s") == 30
    assert _parse_duration("5m 30s") == 330
    assert _parse_duration("1h 2m 3s") == 3723
    assert _parse_duration("-") is None
    assert _parse_duration(None) is None


# --- Integration tests for sync_run_statuses ---


@pytest_asyncio.fixture
async def running_pipeline_run(session, admin_user):
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Monitor Test Experiment",
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
        work_dir="/data/working/nextflow/run-1",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


# --- Phase 20: K8s direct status polling tests ---


@pytest_asyncio.fixture
async def k8s_running_run(session, admin_user):
    """Create a pipeline run with k8s_job_name set (K8s direct polling path)."""
    from app.models.experiment import Experiment
    from app.models.pipeline_run import PipelineRun
    from datetime import datetime, timezone

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="K8s Monitor Test",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=exp.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="bioAF System Test",
        pipeline_version="1.0.0",
        status="running",
        k8s_job_name="bioaf-pipeline-99",
        k8s_namespace="bioaf-pipelines",
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


@pytest.mark.asyncio
async def test_k8s_monitor_uses_adapter_progress_on_completion(session, k8s_running_run):
    """Monitor fetches adapter.get_job_progress() at completion, not while running."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [
            {"name": "STARSOLO", "status": "completed", "cpu": 85.0, "memory_gb": 4.5, "duration_s": 1800},
            {"name": "SAMTOOLS_SORT", "status": "completed", "cpu": 50.0, "memory_gb": 2.1, "duration_s": 300},
            {"name": "FASTQC", "status": "completed", "cpu": 20.0, "memory_gb": 0.5, "duration_s": 270},
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

    mock_compute.get_job_progress.assert_called_once_with(k8s_running_run.k8s_job_name)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    assert run.progress_json is not None
    assert run.progress_json["percent_complete"] == 100.0
    assert run.progress_json["total_processes"] == 3
    assert run.progress_json["completed"] == 3


@pytest.mark.asyncio
async def test_k8s_monitor_creates_process_records_on_completion(session, k8s_running_run):
    """Monitor creates PipelineProcess records from adapter progress at completion."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [
            {"name": "STARSOLO", "status": "completed", "cpu": 85.0, "memory_gb": 4.5, "duration_s": 1800},
            {"name": "SAMTOOLS_SORT", "status": "completed", "cpu": 50.0, "memory_gb": 2.1, "duration_s": 300},
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
    from app.models.pipeline_process import PipelineProcess

    result = await session.execute(select(PipelineProcess).where(PipelineProcess.pipeline_run_id == k8s_running_run.id))
    processes = list(result.scalars().all())
    assert len(processes) == 2
    names = {p.process_name for p in processes}
    assert names == {"STARSOLO", "SAMTOOLS_SORT"}


@pytest.mark.asyncio
async def test_progress_dedupes_retries_by_name(session, k8s_running_run):
    """A task that fails and is retried by Nextflow appears multiple times in
    the trace (one row per attempt). Progress should count unique processes,
    not attempts, so the user sees the actual pipeline shape on the UI."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    # 17 unique tasks; 3 of them have an extra failed attempt before the
    # final completed one. Total trace rows = 17 + 3 = 20. In Nextflow's
    # trace the retried task keeps its task_id and the `attempt` column
    # increments, so dedup is by (task_id, attempt) with the highest
    # attempt as the final state.
    failed = {"status": "failed", "cpu": 0.0, "memory_gb": 0.0, "duration_s": 60}
    completed = {"status": "completed", "cpu": 50.0, "memory_gb": 1.0, "duration_s": 300}
    processes = []
    # 14 tasks that never failed
    for i in range(14):
        processes.append({"task_id": str(i), "attempt": 1, "name": f"TASK_{i}", **completed})
    # 3 retried tasks: failed attempt then completed attempt (same task_id)
    for tid, name in enumerate(("STAR_ALIGN", "STAR_GENOMEGENERATE", "MTX_TO_H5AD"), start=14):
        processes.append({"task_id": str(tid), "attempt": 1, "name": name, **failed})
        processes.append({"task_id": str(tid), "attempt": 2, "name": name, **completed})

    mock_compute.get_job_progress.return_value = {
        "percent_complete": 85.0,  # adapter's raw count gets this wrong; we recompute
        "processes": processes,
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

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    pj = run.progress_json
    assert pj is not None
    assert pj["total_processes"] == 17, f"expected 17 unique tasks, got {pj['total_processes']}"
    assert pj["completed"] == 17, f"expected all 17 completed, got {pj['completed']}"
    assert pj["failed"] == 0, "retried-and-succeeded tasks must not count as failed"
    assert pj["percent_complete"] == 100.0, f"expected 100%, got {pj['percent_complete']}"

    retries = pj.get("retries", [])
    retry_names = {r["name"] for r in retries}
    assert retry_names == {"STAR_ALIGN", "STAR_GENOMEGENERATE", "MTX_TO_H5AD"}
    for r in retries:
        assert r["attempts"] == 2, f"each retry should have 2 attempts, got {r}"


@pytest.mark.asyncio
async def test_progress_no_retries_omits_or_empties_list(session, k8s_running_run):
    """A clean run with no retries should not surface a retries UI."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [
            {"name": "TASK_A", "status": "completed", "cpu": 50.0, "memory_gb": 1.0, "duration_s": 100},
            {"name": "TASK_B", "status": "completed", "cpu": 50.0, "memory_gb": 1.0, "duration_s": 100},
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

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    pj = run.progress_json
    assert pj is not None
    # Either absent or an empty list -- the UI shows the pill only when len > 0.
    assert not pj.get("retries"), f"clean run should have no retries, got {pj.get('retries')!r}"


@pytest.mark.asyncio
async def test_progress_counts_parallel_tasks_with_same_name(session, k8s_running_run):
    """Some pipelines (e.g. nf-core/scrnaseq's MTX_TO_H5AD) run the same
    process multiple times on the same input tag, producing different
    output artifacts each time. The trace then has multiple rows with the
    same `name` but different `task_id`s and `attempt=1` each, meaning
    those are not retries -- they are distinct task executions.

    Progress must count by task_id, not by name. Otherwise a 17-task run
    with 13 unique names (4 of them legitimately repeating) is reported
    as 13/13 in the UI while Nextflow's report shows 17 succeeded.

    Mirrors the run-1 trace from prod where the discrepancy was first
    observed.
    """
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    completed = {"status": "completed", "cpu": 50.0, "memory_gb": 1.0, "duration_s": 300}
    # 11 tasks each with a unique name and unique task_id, no retries.
    processes = [{"task_id": str(i), "attempt": 1, "name": f"TASK_{i}", **completed} for i in range(1, 12)]
    # 3 tasks all named "MTX_TO_H5AD (SAMPLE-101)" -- different task_ids,
    # all attempt=1 -- this is the nf-core/scrnaseq pattern.
    for tid in (12, 13, 14):
        processes.append(
            {
                "task_id": str(tid),
                "attempt": 1,
                "name": "MTX_TO_H5AD (SAMPLE-101)",
                **completed,
            }
        )
    # 3 tasks all named "MTX_TO_SEURAT (SAMPLE-101)" -- same pattern.
    for tid in (15, 16, 17):
        processes.append(
            {
                "task_id": str(tid),
                "attempt": 1,
                "name": "MTX_TO_SEURAT (SAMPLE-101)",
                **completed,
            }
        )
    assert len(processes) == 17

    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": processes,
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

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    pj = run.progress_json
    assert pj is not None
    assert pj["total_processes"] == 17, (
        f"expected 17 unique task_ids, got {pj['total_processes']} "
        "(name-based dedup collapses legitimate parallel runs)"
    )
    assert pj["completed"] == 17
    assert pj["percent_complete"] == 100.0
    assert not pj.get("retries"), f"these are parallel runs, not retries, got {pj.get('retries')!r}"


@pytest.mark.asyncio
async def test_k8s_monitor_keeps_running(session, k8s_running_run):
    """Monitor keeps running status when K8s reports running, does not fetch progress."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "running",
        "pod_name": "bioaf-pipeline-99-xyz",
    }

    with patch(
        "app.services.pipeline_monitor_service.get_compute_adapter",
        return_value=mock_compute,
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    # Progress is only fetched at completion, not while running
    mock_compute.get_job_progress.assert_not_called()

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    assert run.status == "running"
    assert run.k8s_pod_name == "bioaf-pipeline-99-xyz"


@pytest.mark.asyncio
async def test_k8s_monitor_detects_completion(session, k8s_running_run):
    """Test 21: monitor detects K8s job completion."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [],
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

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    assert run.status == "completed"
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_k8s_monitor_detects_failure(session, k8s_running_run):
    """Test 22: monitor detects K8s job failure and populates error_message."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "failed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 0.0,
        "processes": [],
    }
    mock_compute.get_job_logs.return_value = "Error: container exited with code 1"

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == k8s_running_run.id))
    run = result.scalar_one()
    assert run.status == "failed"
    assert "container exited" in run.error_message


@pytest.mark.asyncio
async def test_k8s_monitor_sends_completion_audit(session, k8s_running_run):
    """Test 23: monitor writes audit log on completion."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [],
    }

    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = []

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import text as sql_text

    row = (
        await session.execute(
            sql_text(
                "SELECT action, entity_type FROM audit_log "
                "WHERE entity_type = 'pipeline_run' AND entity_id = :id AND action = 'complete'"
            ).bindparams(id=k8s_running_run.id)
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "complete"


# --- Output file registration on completion ---


@pytest.mark.asyncio
async def test_k8s_completion_registers_output_files(session, k8s_running_run):
    """Completion creates File records for collected outputs."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {
        "status": "completed",
        "pod_name": "bioaf-pipeline-99-xyz",
    }
    mock_compute.get_job_progress.return_value = {
        "percent_complete": 100.0,
        "processes": [],
    }

    run_id = k8s_running_run.id
    exp_id = k8s_running_run.experiment_id
    mock_storage = AsyncMock()
    mock_storage.collect_outputs.return_value = [
        {
            "filename": "filtered.h5ad",
            "gcs_uri": f"gs://bioaf-results-test/experiments/{exp_id}/pipeline-runs/{run_id}/filtered.h5ad",
            "size_bytes": 50_000_000,
            "md5_hash": "abc123",
            "experiment_id": exp_id,
            "pipeline_run_id": run_id,
        },
        {
            "filename": "qc_plot.png",
            "gcs_uri": f"gs://bioaf-results-test/experiments/{exp_id}/pipeline-runs/{run_id}/qc_plot.png",
            "size_bytes": 50_000,
            "md5_hash": "def456",
            "experiment_id": exp_id,
            "pipeline_run_id": run_id,
        },
    ]

    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=mock_compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=mock_storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select
    from app.models.file import File

    result = await session.execute(
        select(File).where(
            File.source_pipeline_run_id == run_id,
            File.source_type == "pipeline_output",
        )
    )
    files = list(result.scalars().all())
    assert len(files) == 2

    filenames = {f.filename for f in files}
    assert filenames == {"filtered.h5ad", "qc_plot.png"}

    h5ad = next(f for f in files if f.filename == "filtered.h5ad")
    assert h5ad.file_type == "h5ad"
    assert h5ad.artifact_type == "anndata"
    assert h5ad.experiment_id == exp_id


# --- Legacy Nextflow trace-based tests ---


COMPLETED_TRACE = """task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\tsubmit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar
1\tab/123\t100\tPROCESS_A\t-\tPROCESS_A\tCOMPLETED\t0\t2026-01-01\t2026-01-01\t2026-01-01\t10m\t9m\t50.0\t1 GB\t2 GB\t0\t0
2\tcd/456\t101\tPROCESS_B\tCOMPLETED\tPROCESS_B\tCOMPLETED\t0\t2026-01-01\t2026-01-01\t2026-01-01\t5m\t4m\t30.0\t500 MB\t1 GB\t0\t0
"""


@pytest.mark.asyncio
async def test_sync_detects_completion(session, running_pipeline_run):
    """Monitor detects when all processes are completed."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "running"}
    mock_compute.get_job_logs.return_value = COMPLETED_TRACE

    with (
        patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_compute,
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    # Refresh the run
    from sqlalchemy import select
    from app.models.pipeline_run import PipelineRun

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == running_pipeline_run.id))
    run = result.scalar_one()
    assert run.status == "completed"
    assert run.progress_json is not None
    assert run.progress_json["total_processes"] == 2
    assert run.progress_json["completed"] == 2
    assert run.progress_json["percent_complete"] == 100.0


FAILED_TRACE = """task_id\thash\tnative_id\tprocess\ttag\tname\tstatus\texit\tsubmit\tstart\tcomplete\tduration\trealtime\t%cpu\tpeak_rss\tpeak_vmem\trchar\twchar
1\tab/123\t100\tPROCESS_A\t-\tPROCESS_A\tCOMPLETED\t0\t2026-01-01\t2026-01-01\t2026-01-01\t10m\t9m\t50.0\t1 GB\t2 GB\t0\t0
2\tcd/456\t101\tPROCESS_B\t-\tPROCESS_B\tFAILED\t1\t2026-01-01\t2026-01-01\t2026-01-01\t5m\t4m\t30.0\t500 MB\t1 GB\t0\t0
"""


@pytest.mark.asyncio
async def test_sync_detects_failure(session, running_pipeline_run):
    """Monitor detects when a process has failed."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "running"}
    mock_compute.get_job_logs.return_value = FAILED_TRACE

    with (
        patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_compute,
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select
    from app.models.pipeline_run import PipelineRun

    result = await session.execute(select(PipelineRun).where(PipelineRun.id == running_pipeline_run.id))
    run = result.scalar_one()
    assert run.status == "failed"
    assert "1 process(es) failed" in run.error_message


@pytest.mark.asyncio
async def test_sync_creates_process_records(session, running_pipeline_run):
    """Monitor creates PipelineProcess records from trace."""
    mock_compute = AsyncMock()
    mock_compute.get_job_status.return_value = {"status": "running"}
    mock_compute.get_job_logs.return_value = COMPLETED_TRACE

    with (
        patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=mock_compute,
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        await PipelineMonitorService.sync_run_statuses(session)

    from sqlalchemy import select
    from app.models.pipeline_process import PipelineProcess

    result = await session.execute(
        select(PipelineProcess).where(PipelineProcess.pipeline_run_id == running_pipeline_run.id)
    )
    processes = list(result.scalars().all())
    assert len(processes) == 2
    assert processes[0].process_name in ("PROCESS_A", "PROCESS_B")
