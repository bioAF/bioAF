"""Characterization: where a launch can leave provider resources the database does not describe.

These pin today's behavior so a later reconciliation change is a visible, deliberate edit. They are
not guarantees: nothing here cleans up, and the gaps they show are deferred follow-up work (see the
module notes in app/services/launch_outcome.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.models.pipeline_run import PipelineRun


@pytest.mark.asyncio
async def test_a_rejected_job_leaves_its_ssh_key_secret_behind(monkeypatch):
    """The per-run SSH key Secret is created before the Job; a Job the API rejects does not remove it."""
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    provider = KubernetesComputeProvider()
    provider._namespace_ready = True
    batch, core = MagicMock(), MagicMock()
    batch.create_namespaced_job.side_effect = RuntimeError("admission webhook denied the request")
    core.read_namespaced_secret.side_effect = ApiException(status=404)
    job_spec = {
        "run_id": 77,
        "pipeline_name": "Custom",
        "container_image": "registry.test/p:v1",
        "command": ["/bin/sh", "-c", "true"],
        "namespace": "bioaf-pipelines",
        "has_code_dir": True,
        "extra_init_containers": [{"name": "clone-repo", "image": "alpine/git", "command": ["true"]}],
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nKEY\n-----END OPENSSH PRIVATE KEY-----",
    }

    with (
        patch.object(provider, "_get_k8s_batch_client", return_value=batch),
        patch.object(provider, "_get_k8s_core_client", return_value=core),
        pytest.raises(RuntimeError),
    ):
        await provider._k8s_submit_job(job_spec)

    created = [
        c.kwargs.get("body", {}).get("metadata", {}).get("name") for c in core.create_namespaced_secret.call_args_list
    ]
    assert "bioaf-ssh-key-77" in created
    assert core.delete_namespaced_secret.call_count == 0


@pytest.mark.asyncio
async def test_an_accepted_job_whose_launch_is_rolled_back_leaves_no_run(session, db_engine, admin_user):
    """The provider has the job, but a caller that rolls back (or whose commit fails) keeps no run
    to track or cancel it. Nothing reconciles the two today."""
    from tests.test_pipeline_launch_failure import _accepting_adapter
    from tests.test_pipeline_reproduction import _launchable_run

    from app.schemas.pipeline_run import PipelineRunLaunchRequest
    from app.services.pipeline_run_service import PipelineRunService

    org_id, user_id = admin_user.organization_id, admin_user.id
    original = await _launchable_run(session, org_id, user_id)
    request = PipelineRunLaunchRequest(
        pipeline_key="nf-core/scrnaseq", experiment_id=original.experiment_id, parameters={"aligner": "cellranger"}
    )
    adapter = _accepting_adapter()

    with (
        patch("app.services.pipeline_run_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        run = await PipelineRunService.launch_run(session, org_id, user_id, request)
    assert run.status == "running"
    await session.rollback()

    assert adapter.submit_job.await_count == 1
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as fresh:
        runs = (await fresh.execute(select(func.count()).select_from(PipelineRun))).scalar_one()
    assert runs == 1  # only the original; the accepted job has no row
