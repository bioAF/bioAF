"""Pipeline and notebook services reach compute, storage and notebooks through the BAL adapters.

Each test runs the service operation against a recording adapter and asserts what the adapter was
asked to do and the state the operation leaves behind, including when the adapter fails. (These
replaced checks that only read the services' source for the adapter getter names.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.adapters.compute.kubernetes import _job_progress_from_dict, _job_status_from_dict
from app.adapters.models import StoredObject, TerminationResult
from app.models.experiment import Experiment
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.services.notebook_service import NotebookService
from app.services.pipeline_monitor_service import PipelineMonitorService
from app.services.pipeline_run_service import PipelineRunService


@dataclass
class RecordingCompute:
    """The compute adapter calls a pipeline run makes, in order; any method can be told to fail."""

    calls: list[tuple] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)
    logs: str = ""

    async def _record(self, name: str, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError(f"{name} unavailable")

    async def persist_job_logs(self, job_id):
        await self._record("persist_job_logs", job_id)

    async def cancel_job(self, job_id):
        await self._record("cancel_job", job_id)

    async def get_job_logs(self, job_id):
        await self._record("get_job_logs", job_id)
        return self.logs


@dataclass
class RecordingNotebooks:
    terminations: list[dict] = field(default_factory=list)
    result: TerminationResult = field(default_factory=TerminationResult)
    fail: bool = False

    async def terminate_session(self, session_id, **kwargs):
        self.terminations.append({"session_id": session_id, **kwargs})
        if self.fail:
            raise RuntimeError("cluster unreachable")
        return self.result


@dataclass
class RecordingStorage:
    collected_from: list[tuple[str, dict]] = field(default_factory=list)
    outputs: list[StoredObject] = field(default_factory=list)

    def build_uri(self, bucket: str, path: str) -> str:
        return f"gs://{bucket}/{path}"

    async def collect_outputs(self, outdir: str, metadata: dict):
        self.collected_from.append((outdir, metadata))
        return self.outputs


@pytest_asyncio.fixture
async def experiment(session, admin_user):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Adapter boundary",
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(exp)
    await session.flush()
    return exp


async def _run(session, admin_user, experiment, **fields) -> PipelineRun:
    values = dict(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.14.0",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    values.update(fields)
    run = PipelineRun(**values)
    session.add(run)
    await session.commit()
    return run


async def _notebook(session, user, **fields) -> NotebookSession:
    values = dict(
        user_id=user.id,
        organization_id=user.organization_id,
        session_type="jupyter",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
        status="running",
        compute_job_ref="bioaf-notebook-7",
        provider_metadata={"namespace": "lab-notebooks", "pod_name": "bioaf-notebook-7"},
        gcs_home_prefix="gs://bioaf-working/notebooks/7/",
    )
    values.update(fields)
    ns = NotebookSession(**values)
    session.add(ns)
    await session.commit()
    return ns


# --- Cancelling a pipeline run ---


@pytest.mark.asyncio
async def test_cancel_saves_the_logs_then_cancels_the_job_through_the_compute_adapter(session, admin_user, experiment):
    run = await _run(session, admin_user, experiment, compute_job_ref="bioaf-pipeline-41")
    compute = RecordingCompute()

    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=compute):
        cancelled = await PipelineRunService.cancel_run(session, run.id, admin_user.id)

    assert compute.calls == [("persist_job_logs", "bioaf-pipeline-41"), ("cancel_job", "bioaf-pipeline-41")]
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["persist_job_logs", "cancel_job"])
async def test_a_failing_adapter_still_leaves_the_run_cancelled(session, admin_user, experiment, failing):
    run = await _run(session, admin_user, experiment, compute_job_ref="bioaf-pipeline-42")
    compute = RecordingCompute(fail={failing})

    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=compute):
        cancelled = await PipelineRunService.cancel_run(session, run.id, admin_user.id)

    assert ("cancel_job", "bioaf-pipeline-42") in compute.calls, "a log failure must not skip the cancel"
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_a_run_never_submitted_is_cancelled_without_calling_compute(session, admin_user, experiment):
    run = await _run(session, admin_user, experiment, status="pending")
    compute = RecordingCompute()

    with patch("app.services.pipeline_run_service.get_compute_adapter", return_value=compute):
        cancelled = await PipelineRunService.cancel_run(session, run.id, admin_user.id)

    assert compute.calls == []
    assert cancelled.status == "cancelled"


# --- Reading a pipeline run's logs ---


@pytest.mark.asyncio
async def test_a_scheduled_runs_logs_come_from_the_compute_adapter_by_job_ref(session, admin_user, experiment):
    run = await _run(session, admin_user, experiment, compute_job_ref="bioaf-pipeline-43")
    compute = RecordingCompute(logs="N E X T F L O W\nexecutor > k8s")

    with patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=compute):
        logs = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert compute.calls == [("get_job_logs", "bioaf-pipeline-43")]
    assert logs == {"stdout": "N E X T F L O W\nexecutor > k8s", "stderr": ""}


@pytest.mark.asyncio
async def test_unreadable_logs_come_back_empty_rather_than_failing(session, admin_user, experiment):
    run = await _run(session, admin_user, experiment, compute_job_ref="bioaf-pipeline-44")
    compute = RecordingCompute(fail={"get_job_logs"})

    with patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=compute):
        logs = await PipelineMonitorService.get_run_logs(session, run.id, "")

    assert logs == {"stdout": "", "stderr": ""}


# --- Completing a pipeline run ---


async def _complete(session, run, storage: RecordingStorage) -> PipelineRun:
    compute = AsyncMock()
    compute.get_job_status.return_value = _job_status_from_dict({"status": "completed", "pod_name": "p-1"})
    compute.get_job_progress.return_value = _job_progress_from_dict({"percent_complete": 100.0, "processes": []})
    with (
        patch("app.services.pipeline_monitor_service.get_compute_adapter", return_value=compute),
        patch("app.services.pipeline_monitor_service.get_storage_adapter", return_value=storage),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        await PipelineMonitorService.sync_run_statuses(session)
    return (await session.execute(select(PipelineRun).where(PipelineRun.id == run.id))).scalar_one()


@pytest.mark.asyncio
async def test_completion_collects_outputs_from_the_runs_outdir_through_storage(session, admin_user, experiment):
    run = await _run(
        session,
        admin_user,
        experiment,
        compute_job_ref="bioaf-pipeline-45",
        k8s_job_name="bioaf-pipeline-45",
        parameters_json={"outdir": "gs://lab-results/experiments/1/pipeline-runs/45"},
    )
    storage = RecordingStorage(
        outputs=[
            StoredObject(filename="multiqc_report.html", storage_uri="gs://lab-results/x/multiqc_report.html"),
        ]
    )

    completed = await _complete(session, run, storage)

    assert storage.collected_from == [
        ("gs://lab-results/experiments/1/pipeline-runs/45", {"id": run.id, "experiment_id": experiment.id})
    ]
    assert completed.status == "completed"
    assert (completed.output_files_json or {}).get("files") == ["multiqc_report.html"]


@pytest.mark.asyncio
async def test_completion_without_an_outdir_collects_from_the_results_bucket(session, admin_user, experiment):
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, "results_bucket_name", "lab-results")
    run = await _run(
        session, admin_user, experiment, compute_job_ref="bioaf-pipeline-46", k8s_job_name="bioaf-pipeline-46"
    )
    storage = RecordingStorage()

    await _complete(session, run, storage)

    assert [outdir for outdir, _ in storage.collected_from] == [
        f"gs://lab-results/experiments/{experiment.id}/pipeline-runs/{run.id}"
    ]


# --- Stopping a notebook session ---


@pytest.mark.asyncio
async def test_stop_terminates_the_session_through_the_notebook_adapter(session, comp_bio_user):
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, "working_bucket_name", "bioaf-working")
    ns = await _notebook(session, comp_bio_user)
    notebooks = RecordingNotebooks(result=TerminationResult(output_prefix="gs://bioaf-working/outputs/7/"))

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks):
        stopped = await NotebookService.stop_session(session, ns.id, comp_bio_user.id)

    assert notebooks.terminations == [
        {
            "session_id": "",
            "pod_name": "bioaf-notebook-7",
            "namespace": "lab-notebooks",
            "gcs_home_prefix": "gs://bioaf-working/notebooks/7/",
            "working_bucket": "bioaf-working",
            "session_type": "jupyter",
        }
    ]
    assert stopped.status == "stopped"
    assert stopped.stopped_at is not None
    assert stopped.gcs_output_prefix == "gs://bioaf-working/outputs/7/"


@pytest.mark.asyncio
async def test_stop_still_stops_the_session_when_termination_fails(session, comp_bio_user):
    ns = await _notebook(session, comp_bio_user)
    notebooks = RecordingNotebooks(fail=True)

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks):
        stopped = await NotebookService.stop_session(session, ns.id, comp_bio_user.id)

    assert len(notebooks.terminations) == 1
    assert stopped.status == "stopped"


# --- The idle-session check ---


@pytest.mark.asyncio
async def test_a_session_idle_past_the_timeout_is_stopped_and_terminated(session, comp_bio_user):
    long_idle = await _notebook(
        session, comp_bio_user, status="idle", idle_since=datetime.now(timezone.utc) - timedelta(hours=5)
    )
    notebooks = RecordingNotebooks()

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks):
        await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)

    await session.refresh(long_idle)
    assert long_idle.status == "stopped"
    assert long_idle.stopped_at is not None
    assert notebooks.terminations == [
        {
            "session_id": "",
            "pod_name": "bioaf-notebook-7",
            "namespace": "lab-notebooks",
            "gcs_home_prefix": "gs://bioaf-working/notebooks/7/",
        }
    ]


@pytest.mark.asyncio
async def test_running_and_recently_idle_sessions_are_left_alone(session, comp_bio_user):
    running = await _notebook(session, comp_bio_user, compute_job_ref="bioaf-notebook-8")
    recent = await _notebook(
        session,
        comp_bio_user,
        compute_job_ref="bioaf-notebook-9",
        status="idle",
        idle_since=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    notebooks = RecordingNotebooks()

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks):
        await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)

    await session.refresh(running)
    await session.refresh(recent)
    assert (running.status, recent.status) == ("running", "idle")
    assert notebooks.terminations == []


@pytest.mark.asyncio
async def test_an_idle_session_is_stopped_even_when_termination_fails(session, comp_bio_user):
    long_idle = await _notebook(
        session, comp_bio_user, status="idle", idle_since=datetime.now(timezone.utc) - timedelta(hours=6)
    )

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=RecordingNotebooks(fail=True)):
        await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)

    await session.refresh(long_idle)
    assert long_idle.status == "stopped"


# --- A stopped session's outputs are kept: registered, then moved to the results bucket ---


@dataclass
class InMemoryBuckets:
    """A storage adapter over in-memory buckets: uri -> size. copy can be told to fail."""

    objects: dict[str, int] = field(default_factory=dict)
    fail_copy: bool = False

    def build_uri(self, bucket: str, key: str) -> str:
        return f"gs://{bucket}/{key.lstrip('/')}"

    def parse_uri(self, uri: str) -> tuple[str, str]:
        bucket, _, key = uri.removeprefix("gs://").partition("/")
        return bucket, key

    async def list_objects(self, prefix: str):
        return [
            StoredObject(filename=uri.rsplit("/", 1)[1], storage_uri=uri, size_bytes=size)
            for uri, size in sorted(self.objects.items())
            if uri.startswith(prefix)
        ]

    async def copy(self, src: str, dst: str):
        if self.fail_copy:
            raise RuntimeError("copy refused")
        self.objects[dst] = self.objects[src]

    async def delete(self, uri: str):
        del self.objects[uri]


async def _stop_with_outputs(session, user, buckets: InMemoryBuckets):
    from app.models.file import File
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, "working_bucket_name", "lab-working")
    await PlatformConfigService.set(session, "results_bucket_name", "lab-results")
    ns = await _notebook(session, user)
    prefix = f"gs://lab-working/sessions/{ns.id}/"
    buckets.objects.update({f"{prefix}umap.png": 2048, f"{prefix}clusters.h5ad": 9000})
    outputs = [
        StoredObject(filename=u.rsplit("/", 1)[1], storage_uri=u, size_bytes=s) for u, s in buckets.objects.items()
    ]
    notebooks = RecordingNotebooks(result=TerminationResult(output_files=outputs, output_prefix=prefix))

    with (
        patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks),
        patch("app.adapters.registry.get_storage_adapter", return_value=buckets),
    ):
        stopped = await NotebookService.stop_session(session, ns.id, user.id)
    await session.commit()
    files = (
        (await session.execute(select(File).where(File.source_notebook_session_id == ns.id).order_by(File.filename)))
        .scalars()
        .all()
    )
    return ns.id, stopped, files


@pytest.mark.asyncio
async def test_stopping_registers_the_sessions_outputs_and_moves_them_to_results(session, comp_bio_user):
    buckets = InMemoryBuckets()
    session_id, stopped, files = await _stop_with_outputs(session, comp_bio_user, buckets)

    results = f"gs://lab-results/sessions/{session_id}/"
    assert [(f.filename, f.storage_uri, f.size_bytes) for f in files] == [
        ("clusters.h5ad", f"{results}clusters.h5ad", 9000),
        ("umap.png", f"{results}umap.png", 2048),
    ]
    assert sorted(buckets.objects) == [f"{results}clusters.h5ad", f"{results}umap.png"]  # working copies removed
    assert stopped.gcs_output_prefix == results
    assert stopped.status == "stopped"


@pytest.mark.asyncio
async def test_a_failed_move_keeps_the_outputs_registered_where_they_are(session, comp_bio_user):
    buckets = InMemoryBuckets(fail_copy=True)
    session_id, stopped, files = await _stop_with_outputs(session, comp_bio_user, buckets)

    working = f"gs://lab-working/sessions/{session_id}/"
    assert [f.storage_uri for f in files] == [f"{working}clusters.h5ad", f"{working}umap.png"]
    assert sorted(buckets.objects) == [f"{working}clusters.h5ad", f"{working}umap.png"]  # nothing deleted
    assert stopped.gcs_output_prefix == working
    assert stopped.status == "stopped"


# --- A notebook launch the adapter refuses ---


@dataclass
class RefusingNotebooks:
    error: str

    async def launch_session(self, spec):
        raise RuntimeError(self.error)


@pytest.mark.asyncio
async def test_a_refused_launch_leaves_a_failed_session_with_a_reason(session, comp_bio_user):
    with patch(
        "app.services.notebook_service.get_notebook_adapter",
        return_value=RefusingNotebooks("ZONE_RESOURCE_POOL_EXHAUSTED: no capacity in us-central1-a"),
    ):
        ns = await NotebookService.launch_session(
            session,
            user_id=comp_bio_user.id,
            org_id=comp_bio_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
        )

    assert ns.status == "failed"
    assert ns.failure_reason
    assert ns.failure_message


@pytest.mark.asyncio
async def test_a_launch_on_an_inaccessible_image_is_raised_to_the_caller(session, comp_bio_user):
    with (
        patch(
            "app.services.notebook_service.get_notebook_adapter",
            return_value=RefusingNotebooks("image registry/p:1 not found or not accessible"),
        ),
        pytest.raises(RuntimeError, match="not found or not accessible"),
    ):
        await NotebookService.launch_session(
            session,
            user_id=comp_bio_user.id,
            org_id=comp_bio_user.organization_id,
            session_type="jupyter",
            resource_profile="small",
        )


# --- Smaller edges of the same promises ---


@pytest.mark.asyncio
async def test_cancelling_an_unknown_run_is_not_found(session, admin_user):
    from app.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await PipelineRunService.cancel_run(session, 999999, admin_user.id)


@pytest.mark.asyncio
async def test_completion_without_an_outdir_or_results_bucket_collects_from_local_results(
    session, admin_user, experiment
):
    run = await _run(
        session, admin_user, experiment, compute_job_ref="bioaf-pipeline-47", k8s_job_name="bioaf-pipeline-47"
    )
    storage = RecordingStorage()

    await _complete(session, run, storage)

    assert [outdir for outdir, _ in storage.collected_from] == [
        f"/data/results/experiments/{experiment.id}/pipeline-runs/{run.id}"
    ]


@pytest.mark.asyncio
async def test_an_idle_session_with_no_compute_job_is_stopped_without_calling_the_adapter(session, comp_bio_user):
    orphan = await _notebook(
        session,
        comp_bio_user,
        compute_job_ref=None,
        status="idle",
        idle_since=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    notebooks = RecordingNotebooks()

    with patch("app.services.notebook_service.get_notebook_adapter", return_value=notebooks):
        await NotebookService.check_idle_sessions(session, idle_timeout_hours=4)

    await session.refresh(orphan)
    assert orphan.status == "stopped"
    assert notebooks.terminations == []
