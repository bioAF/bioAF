"""Tests for detecting a pipeline whose tasks can never be scheduled.

Run 44's eleven task pods sat Pending for 35 minutes against a node pool that
could not create a single node. bioAF reported the run as `running` the whole
time, because `_k8s_get_job_status` selects pods by `job-name={job_id}` and that
matches only the Nextflow HEAD pod. The head was healthy. The tasks were invisible
by construction, and a working run and a dead one looked identical.

Two halves:

1. Task pods must carry the run id, or they cannot be attributed at all. Nextflow
   sets no bioAF labels of its own, and several runs share one namespace.
2. The monitor must fail a run whose tasks are continuously unschedulable, naming
   that as the reason.

The grace period exists because Pending is normal: nodes take time to provision
(90 seconds, observed) and Spot capacity retries move across zones. Only
CONTINUOUS unschedulability counts, so a pod that schedules resets the clock.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.models.pipeline_run import PipelineRun
from app.services.pipeline_monitor_service import (
    UNSCHEDULABLE_GRACE_SECONDS,
    PipelineMonitorService,
)


# -- half one: task pods must be attributable to their run -------------------


class TestTaskPodsCarryTheRunId:
    def test_generated_config_labels_task_pods_with_the_run(self):
        """Without this, a Pending pod cannot be tied to the run that created it."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test/nextflow-work",
            run_id=44,
        )
        assert "bioaf.io/run-id" in config
        assert "44" in config

    def test_the_label_is_a_pod_directive_so_it_lands_on_task_pods(self):
        """`process.pod` is what the k8s executor turns into pod metadata."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test/nextflow-work",
            run_id=44,
        )
        pod_lines = [ln for ln in config.splitlines() if "bioaf.io/run-id" in ln]
        assert pod_lines, "run-id label must be emitted"
        assert any("pod" in ln for ln in pod_lines)

    def test_a_run_without_an_id_emits_no_label_rather_than_a_broken_one(self):
        """Legacy callers must not produce `bioaf.io/run-id: None`."""
        config = KubernetesComputeProvider._build_nextflow_k8s_config(
            namespace="bioaf-pipelines",
            has_gcs_secret=True,
            gcs_work_dir="gs://bioaf-raw-test/nextflow-work",
        )
        assert "bioaf.io/run-id" not in config


# -- half two: the monitor acts on it ----------------------------------------


@pytest_asyncio.fixture
async def running_k8s_run(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Unschedulable Test Experiment",
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
        status="running",
        compute_job_ref="bioaf-pipeline-44",
    )
    session.add(run)
    await session.flush()
    return run


def _adapter(scheduling):
    adapter = MagicMock()
    adapter.get_job_status = AsyncMock(
        return_value=MagicMock(status="running", provider_details={"pod_name": "bioaf-pipeline-44-x"})
    )
    adapter.get_task_scheduling = AsyncMock(return_value=scheduling)
    return adapter


def _unschedulable(count=11, reason="quota_exceeded"):
    return {
        "tasks": count,
        "scheduled": 0,
        "unschedulable": count,
        "reason": reason,
        "message": "Insufficient quota: SSD_TOTAL_GB exceeded",
    }


class TestUnschedulableRunsFail:
    @pytest.mark.asyncio
    async def test_briefly_unschedulable_does_not_fail_the_run(self, session, running_k8s_run):
        """Nodes take time. 90 seconds of Pending was the normal, healthy case."""
        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(_unschedulable()),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.status == "running"

    @pytest.mark.asyncio
    async def test_continuously_unschedulable_past_the_grace_period_fails(self, session, running_k8s_run):
        started = datetime.now(timezone.utc) - timedelta(seconds=UNSCHEDULABLE_GRACE_SECONDS + 60)
        running_k8s_run.provider_metadata = {"unschedulable_since": started.isoformat()}

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(_unschedulable()),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.status == "failed"

    @pytest.mark.asyncio
    async def test_the_failure_names_unschedulability_not_a_generic_error(self, session, running_k8s_run):
        """`analysis run failed` is what study 11 recorded and it explains nothing."""
        started = datetime.now(timezone.utc) - timedelta(seconds=UNSCHEDULABLE_GRACE_SECONDS + 60)
        running_k8s_run.provider_metadata = {"unschedulable_since": started.isoformat()}

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(_unschedulable()),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.failure_reason == "quota_exceeded"
        assert "SSD_TOTAL_GB" in (running_k8s_run.error_message or "")

    @pytest.mark.asyncio
    async def test_a_pod_scheduling_resets_the_clock(self, session, running_k8s_run):
        """Spot capacity moves between zones; partial progress is not a dead run."""
        started = datetime.now(timezone.utc) - timedelta(seconds=UNSCHEDULABLE_GRACE_SECONDS + 60)
        running_k8s_run.provider_metadata = {"unschedulable_since": started.isoformat()}
        partly_running = {
            "tasks": 11,
            "scheduled": 3,
            "unschedulable": 8,
            "reason": "quota_exceeded",
            "message": "Insufficient quota",
        }

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(partly_running),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.status == "running"
        assert (running_k8s_run.provider_metadata or {}).get("unschedulable_since") is None

    @pytest.mark.asyncio
    async def test_a_run_whose_tasks_cannot_be_attributed_is_never_failed(self, session, running_k8s_run):
        """Runs launched before the run-id label carry no attributable pods.

        Failing them on evidence that cannot be tied to them would be worse than
        the blindness this replaces.
        """
        started = datetime.now(timezone.utc) - timedelta(seconds=UNSCHEDULABLE_GRACE_SECONDS + 600)
        running_k8s_run.provider_metadata = {"unschedulable_since": started.isoformat()}

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(None),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.status == "running"

    @pytest.mark.asyncio
    async def test_a_run_with_no_tasks_yet_is_not_failed(self, session, running_k8s_run):
        """Between submit and the first task pod there is nothing to judge."""
        empty = {"tasks": 0, "scheduled": 0, "unschedulable": 0, "reason": "", "message": ""}

        with patch(
            "app.services.pipeline_monitor_service.get_compute_adapter",
            return_value=_adapter(empty),
        ):
            await PipelineMonitorService._sync_k8s_run(session, running_k8s_run, "bioaf-pipeline-44")

        assert running_k8s_run.status == "running"
