"""Tests for SSH connection command endpoints."""

from types import SimpleNamespace

import sqlalchemy as sa
from kubernetes.client.exceptions import ApiException

from app.adapters.capabilities import ProviderCapabilities
from app.adapters import registry as adapter_registry
from app.models.audit_log import AuditLog
from app.models.organization import Organization
from app.services import api_key_service, role_service, service_account_service
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.adapters.compute.kubernetes import KubernetesComputeProvider
from app.adapters.kubernetes import exec_access
from app.adapters.models import JobSubmitResult
from app.adapters.notebooks.kubernetes import KubernetesNotebookProvider
from app.api import ssh_connect
from app.models.experiment import Experiment
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.services.auth_service import AuthService
from app.services.launch_outcome import record_submission_accepted
from app.settings_contract import ComputeMode
from tests.support.exec_access_fakes import GKE, FakeK8s, head_pod, notebook_pod, pipeline_job, task_pod


@pytest.fixture(autouse=True)
def live_cluster(monkeypatch):
    """Kubernetes adapters in live mode, reading a fake cluster that fixtures populate.

    Connect endpoints verify the workload against the cluster, so a record alone no longer
    produces instructions: each fixture below also creates the matching Job/Pod here.
    """
    fake = FakeK8s()
    compute = KubernetesComputeProvider(mode=ComputeMode.KUBERNETES)
    notebooks = KubernetesNotebookProvider(mode=ComputeMode.KUBERNETES)
    for adapter in (compute, notebooks):
        adapter._cluster_config = dict(GKE)
        monkeypatch.setattr(adapter, "_connection_clients", lambda: (fake, fake))
    monkeypatch.setattr(exec_access, "backend_for_cluster_auth", lambda: "gke")
    monkeypatch.setattr(ssh_connect, "get_compute_adapter", lambda: compute)
    monkeypatch.setattr(ssh_connect, "get_notebook_adapter", lambda: notebooks)
    # The registry's adapters are simulated in tests; the live compute adapter declares ssh_exec.
    monkeypatch.setattr(adapter_registry, "get_active_capabilities", compute.capabilities)
    return SimpleNamespace(fake=fake, compute=compute, notebooks=notebooks)


def launch_run(run: PipelineRun, live, namespace: str = "bioaf-pipelines") -> str:
    """Record the launch the way record_submission_accepted does and create its Job + head pod."""
    job = f"bioaf-pipeline-{run.id}-1731700000"
    record_submission_accepted(
        run, JobSubmitResult(job_id=job, status="running", provider_details={"namespace": namespace})
    )
    run.status = "running"
    live.fake.job = pipeline_job(run_id=str(run.id), name=job, namespace=namespace)
    live.fake.pods = [task_pod(), head_pod(job=job, namespace=namespace)]
    return job


def launch_notebook(nb: NotebookSession, live, namespace: str = "bioaf-notebooks") -> str:
    """Record the launch the way NotebookService.launch_session does and create its Pod."""
    pod = f"bioaf-notebook-{nb.id}"
    nb.slurm_job_id = str(nb.id)
    nb.k8s_pod_name = pod
    nb.k8s_namespace = namespace
    nb.compute_job_ref = pod
    nb.provider_metadata = {"pod_name": pod, "namespace": namespace}
    live.fake.pod = notebook_pod(session=str(nb.id), user=str(nb.user_id), kind=nb.session_type, namespace=namespace)
    return pod


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbio123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def bench_token(bench_user) -> str:
    return AuthService.create_token(
        bench_user.id,
        bench_user.email,
        bench_user.role_id,
        bench_user.organization_id,
        role_name="bench",
    )


@pytest_asyncio.fixture
async def running_pipeline_run(session, admin_user, live_cluster):
    """Create an experiment and a running pipeline run."""
    experiment = Experiment(
        name="Test Experiment",
        organization_id=admin_user.organization_id,
        owner_user_id=admin_user.id,
        status="processing",
    )
    session.add(experiment)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.12.0",
        status="running",
    )
    session.add(run)
    await session.flush()
    launch_run(run, live_cluster)
    await session.commit()
    return run


@pytest_asyncio.fixture
async def completed_pipeline_run(session, admin_user):
    """Create a completed pipeline run."""
    experiment = Experiment(
        name="Test Experiment 2",
        organization_id=admin_user.organization_id,
        owner_user_id=admin_user.id,
        status="complete",
    )
    session.add(experiment)
    await session.flush()

    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="nf-core/rnaseq",
        pipeline_version="3.12.0",
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.commit()
    return run


@pytest_asyncio.fixture
async def running_notebook_session(session, admin_user, live_cluster):
    """Create a running notebook session."""
    nb_session = NotebookSession(
        user_id=admin_user.id,
        organization_id=admin_user.organization_id,
        session_type="jupyter",
        status="running",
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
    )
    session.add(nb_session)
    await session.flush()
    launch_notebook(nb_session, live_cluster)
    await session.commit()
    return nb_session


@pytest.mark.asyncio
async def test_connect_running_pipeline_run(client, admin_token, running_pipeline_run):
    """Test connect endpoint returns command for a running pipeline job."""
    resp = await client.post(
        f"/api/pipeline-runs/{running_pipeline_run.id}/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "kubectl exec" in data["command"]
    assert data["target_type"] == "pipeline_job"
    assert data["namespace"] == "bioaf-pipelines"
    assert data["setup_guide"] != ""
    assert data["warning"] != ""


@pytest.mark.asyncio
async def test_connect_completed_pipeline_run_409(client, admin_token, completed_pipeline_run):
    """Test connect endpoint returns 409 for a completed pipeline job."""
    resp = await client.post(
        f"/api/pipeline-runs/{completed_pipeline_run.id}/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_connect_nonexistent_pipeline_run_404(client, admin_token):
    """Test connect endpoint returns 404 for a non-existent job."""
    resp = await client.post(
        "/api/pipeline-runs/99999/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_connect_writes_audit_log(client, admin_token, running_pipeline_run, session):
    """Test connect endpoint writes audit log entry."""
    resp = await client.post(
        f"/api/pipeline-runs/{running_pipeline_run.id}/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    result = await session.execute(
        text(
            "SELECT * FROM audit_log WHERE entity_type = 'container_session' "
            "AND action = 'connection_command_generated' "
            f"AND entity_id = {running_pipeline_run.id}"
        )
    )
    audit_entry = result.fetchone()
    assert audit_entry is not None


@pytest.mark.asyncio
async def test_connect_requires_comp_bio_or_admin(client, bench_token, running_pipeline_run):
    """Test connect endpoint requires comp_bio or admin role (403 for bench)."""
    resp = await client.post(
        f"/api/pipeline-runs/{running_pipeline_run.id}/connect",
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_connect_allowed_for_comp_bio(client, comp_bio_token, running_pipeline_run):
    """Test connect endpoint allowed for comp_bio role."""
    resp = await client.post(
        f"/api/pipeline-runs/{running_pipeline_run.id}/connect",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 200
    assert "kubectl exec" in resp.json()["command"]


@pytest.mark.asyncio
async def test_pods_listing_admin_only(client, admin_token):
    """Test pods listing returns running pods (admin only)."""
    resp = await client.get(
        "/api/infrastructure/compute/pods",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "pods" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_pods_listing_403_non_admin(client, comp_bio_token):
    """Test pods listing returns 403 for non-admin users."""
    resp = await client.get(
        "/api/infrastructure/compute/pods",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_connect_notebook_session(client, admin_token, running_notebook_session):
    """Test connect endpoint returns command for a running notebook session."""
    resp = await client.post(
        f"/api/sessions/{running_notebook_session.id}/connect",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "kubectl exec" in data["command"]
    assert data["target_type"] == "notebook_session"
    assert data["namespace"] == "bioaf-notebooks"


# -- repaired behavior -----------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _audits(session, record_id: int) -> list[AuditLog]:
    session.expire_all()
    rows = await session.execute(
        sa.select(AuditLog).where(
            AuditLog.entity_type == "container_session",
            AuditLog.action == "connection_command_generated",
            AuditLog.entity_id == record_id,
        )
    )
    return list(rows.scalars())


async def _notebook(session, admin_user, live, *, session_type="jupyter", status="running", org_id=None, launch=True):
    nb = NotebookSession(
        user_id=admin_user.id,
        organization_id=org_id or admin_user.organization_id,
        session_type=session_type,
        status=status,
        resource_profile="small",
        cpu_cores=2,
        memory_gb=4,
    )
    session.add(nb)
    await session.flush()
    if launch:
        launch_notebook(nb, live)
    await session.commit()
    return nb


async def _custom_headers(session, org_id: int, email: str, grants: list[tuple[str, str]]) -> dict:
    from app.models.user import User

    role = await role_service.create_role(session, org_id, f"custom-{email}", permissions=grants)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("customrolepass1"),
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    return _auth(AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name))


async def _key_headers(session, admin_user, scopes: list[str]) -> dict:
    account = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name=f"Key {','.join(scopes)}",
        role_id=admin_user.role_id,
        created_by_user_id=admin_user.id,
    )
    _row, secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=account.id,
        name="key",
        scopes=scopes,
        created_by_user_id=admin_user.id,
    )
    await session.commit()
    return _auth(secret)


@pytest.mark.asyncio
async def test_pipeline_instructions_name_the_timestamped_job_head_pod_and_recorded_namespace(
    client, admin_token, session, admin_user, live_cluster
):
    experiment = Experiment(name="E", organization_id=admin_user.organization_id, owner_user_id=admin_user.id)
    session.add(experiment)
    await session.flush()
    run = PipelineRun(
        organization_id=admin_user.organization_id,
        experiment_id=experiment.id,
        submitted_by_user_id=admin_user.id,
        pipeline_name="custom: my-tool",
        status="running",
    )
    session.add(run)
    await session.flush()
    job = launch_run(run, live_cluster, namespace="team-pipelines")
    await session.commit()
    run_id = run.id

    resp = await client.post(f"/api/pipeline-runs/{run_id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_type"] == "pipeline_job"
    assert data["target_id"] == job
    assert data["namespace"] == "team-pipelines"
    assert data["command"] == (
        f"kubectl exec -it --context gke_lab-proj-1_us-west1_bioaf-lab-abc123 -n team-pipelines {job}-x7k2p "
        "--container pipeline -- /bin/sh"
    )
    assert "get-credentials bioaf-lab-abc123 --region us-west1 --project lab-proj-1" in data["setup_guide"]
    assert "not tracked" in data["warning"] and "request new instructions" in data["warning"]

    [audit] = await _audits(session, run_id)
    assert audit.details_json == {
        "target_type": "pipeline_job",
        "record_id": run_id,
        "target_id": job,
        "provider_ref": job,
        "pod_name": f"{job}-x7k2p",
        "namespace": "team-pipelines",
        "container": "pipeline",
        "context": "gke_lab-proj-1_us-west1_bioaf-lab-abc123",
        "pipeline_name": "custom: my-tool",
    }


@pytest.mark.parametrize("session_type", ["jupyter", "rstudio"])
@pytest.mark.parametrize("status", ["running", "idle"])
@pytest.mark.asyncio
async def test_notebook_instructions_use_a_single_prefix_and_the_notebook_container(
    client, admin_token, session, admin_user, live_cluster, session_type, status
):
    nb = await _notebook(session, admin_user, live_cluster, session_type=session_type, status=status)
    nb_id = nb.id

    resp = await client.post(f"/api/sessions/{nb_id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_id"] == f"bioaf-notebook-{nb_id}"
    assert "notebook-notebook" not in data["command"]
    assert f" bioaf-notebook-{nb_id} --container notebook -- /bin/sh" in data["command"]
    [audit] = await _audits(session, nb_id)
    assert audit.details_json["pod_name"] == f"bioaf-notebook-{nb_id}"
    assert audit.details_json["namespace"] == "bioaf-notebooks"
    assert audit.details_json["session_type"] == session_type


@pytest.mark.asyncio
async def test_legacy_notebook_without_recorded_pod_uses_the_launch_rule(
    client, admin_token, session, admin_user, live_cluster
):
    nb = await _notebook(session, admin_user, live_cluster, launch=False)
    live_cluster.fake.pod = notebook_pod(session=str(nb.id), user=str(admin_user.id))

    resp = await client.post(f"/api/sessions/{nb.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["target_id"] == f"bioaf-notebook-{nb.id}"
    assert resp.json()["namespace"] == "bioaf-notebooks"


@pytest.mark.parametrize(
    "session_type,status,code,reason",
    [
        ("ssh", "running", "connection_target_invalid", "unsupported_session_type"),
        ("headless", "running", "connection_target_invalid", "unsupported_session_type"),
        ("jupyter", "pending", "connection_unavailable", "not_running"),
        ("rstudio", "stopped", "connection_unavailable", "not_running"),
    ],
)
@pytest.mark.asyncio
async def test_ineligible_sessions_are_refused_without_a_provider_call_or_audit(
    client, admin_token, session, admin_user, live_cluster, session_type, status, code, reason
):
    nb = await _notebook(session, admin_user, live_cluster, session_type=session_type, status=status)

    resp = await client.post(f"/api/sessions/{nb.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 409
    assert resp.json()["code"] == code
    assert resp.json()["details"] == {"reason": reason}
    assert "command" not in resp.json()
    assert live_cluster.fake.calls == []
    assert await _audits(session, nb.id) == []


@pytest.mark.asyncio
async def test_other_organizations_records_are_not_found(client, admin_token, session, admin_user, live_cluster):
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    nb = await _notebook(session, admin_user, live_cluster, org_id=other.id)
    run = PipelineRun(organization_id=other.id, pipeline_name="x", status="running")
    session.add(run)
    await session.flush()
    launch_run(run, live_cluster)
    await session.commit()

    for path in (f"/api/sessions/{nb.id}/connect", f"/api/pipeline-runs/{run.id}/connect"):
        resp = await client.post(path, headers=_auth(admin_token))
        assert resp.status_code == 404
    missing = await client.post("/api/sessions/999999/connect", headers=_auth(admin_token))
    assert missing.status_code == 404
    assert live_cluster.fake.calls == []


@pytest.mark.asyncio
async def test_simulated_mode_returns_no_kubernetes_command(
    client, admin_token, running_notebook_session, session, monkeypatch
):
    # Simulated compute declares no ssh_exec, so the public path refuses before any adapter call.
    # (The adapters also refuse simulated mode themselves: test_exec_access_resolution.)
    monkeypatch.setattr(ssh_connect, "get_notebook_adapter", lambda: KubernetesNotebookProvider(mode=ComputeMode.LOCAL))
    monkeypatch.setattr(
        adapter_registry, "get_active_capabilities", KubernetesComputeProvider(mode=ComputeMode.LOCAL).capabilities
    )

    resp = await client.post(f"/api/sessions/{running_notebook_session.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 422
    assert resp.json()["code"] == "capability_not_supported"
    assert await _audits(session, running_notebook_session.id) == []


@pytest.mark.asyncio
async def test_unsupported_ssh_exec_capability_is_422(
    client, admin_token, running_pipeline_run, live_cluster, monkeypatch
):
    monkeypatch.setattr(adapter_registry, "get_active_capabilities", lambda: ProviderCapabilities())

    resp = await client.post(f"/api/pipeline-runs/{running_pipeline_run.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 422
    assert resp.json()["code"] == "capability_not_supported"
    assert live_cluster.fake.calls == []


@pytest.mark.asyncio
async def test_a_pipeline_without_a_recorded_job_is_refused(client, admin_token, session, completed_pipeline_run):
    completed_pipeline_run.status = "running"
    session.add(completed_pipeline_run)
    await session.commit()

    resp = await client.post(f"/api/pipeline-runs/{completed_pipeline_run.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 409
    assert resp.json()["details"] == {"reason": "missing_identity"}


@pytest.mark.parametrize(
    "grants,expected",
    [([("notebooks", "launch")], 200), ([("notebooks", "view"), ("pipelines", "launch")], 403)],
)
@pytest.mark.asyncio
async def test_custom_role_eligibility_follows_the_notebooks_launch_grant(
    client, session, admin_user, running_notebook_session, live_cluster, grants, expected
):
    headers = await _custom_headers(session, admin_user.organization_id, "custom@test.com", grants)

    resp = await client.post(f"/api/sessions/{running_notebook_session.id}/connect", headers=headers)

    assert resp.status_code == expected
    if expected == 403:
        assert live_cluster.fake.calls == []
        assert await _audits(session, running_notebook_session.id) == []


@pytest.mark.asyncio
async def test_api_keys_cannot_obtain_instructions_outside_their_scope_envelope(
    client, session, admin_user, running_pipeline_run, running_notebook_session, live_cluster
):
    # The key's service account holds the admin role, but notebooks:launch is outside the public
    # scope alphabet, so no key can carry it.
    headers = await _key_headers(session, admin_user, ["samples:view"])

    for path in (
        f"/api/pipeline-runs/{running_pipeline_run.id}/connect",
        f"/api/sessions/{running_notebook_session.id}/connect",
    ):
        resp = await client.post(path, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "key_scope_missing"
    assert live_cluster.fake.calls == []


@pytest.mark.parametrize(
    "error,status,code,reason",
    [
        (ApiException(status=403), 502, "connection_provider_unavailable", "provider_denied"),
        (ApiException(status=500), 502, "connection_provider_unavailable", "provider_unavailable"),
        (ApiException(status=404), 409, "connection_unavailable", "workload_missing"),
    ],
)
@pytest.mark.asyncio
async def test_provider_failures_return_no_command_and_no_audit(
    client, admin_token, session, running_pipeline_run, live_cluster, error, status, code, reason
):
    live_cluster.fake.error = error

    resp = await client.post(f"/api/pipeline-runs/{running_pipeline_run.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == status
    assert resp.json()["code"] == code
    assert resp.json()["details"] == {"reason": reason}
    assert "command" not in resp.json()
    assert await _audits(session, running_pipeline_run.id) == []


@pytest.mark.asyncio
async def test_stopped_main_container_is_unavailable(
    client, admin_token, session, running_notebook_session, live_cluster
):
    live_cluster.fake.pod = notebook_pod(
        session=str(running_notebook_session.id),
        user=str(running_notebook_session.user_id),
        notebook="terminated",
    )

    resp = await client.post(f"/api/sessions/{running_notebook_session.id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 409
    assert resp.json()["details"] == {"reason": "container_not_running"}


@pytest.mark.parametrize("change", ["stopped", "reference"])
@pytest.mark.asyncio
async def test_a_record_that_changes_during_resolution_is_refused(
    client, admin_token, session, db_engine, running_notebook_session, live_cluster, monkeypatch, change
):
    nb_id = running_notebook_session.id
    original = live_cluster.notebooks.describe_connection

    async def racing(target):
        description = await original(target)
        async with db_engine.connect() as conn:
            if change == "stopped":
                await conn.execute(sa.text("UPDATE compute_sessions SET status='stopped' WHERE id=:i"), {"i": nb_id})
            else:
                await conn.execute(
                    sa.text(
                        "UPDATE compute_sessions SET compute_job_ref='bioaf-notebook-x', k8s_pod_name='bioaf-notebook-x', provider_metadata=NULL WHERE id=:i"
                    ),
                    {"i": nb_id},
                )
            await conn.commit()
        return description

    monkeypatch.setattr(live_cluster.notebooks, "describe_connection", racing)

    resp = await client.post(f"/api/sessions/{nb_id}/connect", headers=_auth(admin_token))

    assert resp.status_code == 409
    assert resp.json()["details"] == {"reason": "state_changed"}
    assert "command" not in resp.json()
    assert await _audits(session, nb_id) == []


@pytest.mark.asyncio
async def test_an_audit_failure_prevents_a_successful_response(client, admin_token, running_pipeline_run, monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(ssh_connect, "log_action", broken)

    try:
        resp = await client.post(f"/api/pipeline-runs/{running_pipeline_run.id}/connect", headers=_auth(admin_token))
    except RuntimeError:
        return
    assert resp.status_code >= 500
    assert "command" not in resp.text


# -- administrator inventory -------------------------------------------------------------------


@pytest.fixture
def no_provider_listing(live_cluster, monkeypatch):
    async def refuse(*args, **kwargs):
        raise AssertionError("the inventory must not list provider-wide workloads")

    monkeypatch.setattr(live_cluster.compute, "list_jobs", refuse)
    monkeypatch.setattr(live_cluster.notebooks, "list_sessions", refuse)
    return live_cluster


@pytest.mark.asyncio
async def test_inventory_lists_only_this_organizations_resolvable_targets(
    client, admin_token, session, admin_user, no_provider_listing
):
    live = no_provider_listing
    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()

    def run(org, status="running", **fields):
        r = PipelineRun(organization_id=org, pipeline_name="p", status=status, **fields)
        session.add(r)
        return r

    good_run = run(org_id)
    nondefault_run = run(org_id)
    run(org_id, status="completed", compute_job_ref="bioaf-pipeline-9-1")
    run(org_id)  # running but no recorded job: unresolved, left out
    run(org_id, compute_job_ref="bioaf-pipeline-1-1", k8s_job_name="bioaf-pipeline-1-2")  # conflicting
    run(other.id, compute_job_ref="bioaf-pipeline-77-1")
    await session.flush()
    good_job = launch_run(good_run, live)
    other_job = launch_run(nondefault_run, live, namespace="team-pipelines")

    jupyter = await _notebook(session, admin_user, live)
    rstudio = await _notebook(session, admin_user, live, session_type="rstudio", status="idle")
    legacy = await _notebook(session, admin_user, live, launch=False)
    await _notebook(session, admin_user, live, session_type="ssh")
    await _notebook(session, admin_user, live, session_type="headless")
    await _notebook(session, admin_user, live, status="stopped")
    await _notebook(session, admin_user, live, org_id=other.id)
    await session.commit()
    ids = {
        "good": good_run.id,
        "nondefault": nondefault_run.id,
        "jupyter": jupyter.id,
        "rstudio": rstudio.id,
        "legacy": legacy.id,
    }

    resp = await client.get("/api/infrastructure/compute/pods", headers=_auth(admin_token))

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == len(data["pods"]) == 5
    assert {(p["record_id"], p["type"], p["name"], p["namespace"], p["status"]) for p in data["pods"]} == {
        (ids["good"], "pipeline_job", good_job, "bioaf-pipelines", "running"),
        (ids["nondefault"], "pipeline_job", other_job, "team-pipelines", "running"),
        (ids["jupyter"], "notebook_session", f"bioaf-notebook-{ids['jupyter']}", "bioaf-notebooks", "running"),
        (ids["rstudio"], "notebook_session", f"bioaf-notebook-{ids['rstudio']}", "bioaf-notebooks", "idle"),
        (ids["legacy"], "notebook_session", f"bioaf-notebook-{ids['legacy']}", "bioaf-notebooks", "running"),
    }
    assert live.fake.calls == []


@pytest.mark.asyncio
async def test_inventory_is_empty_when_nothing_is_eligible(client, admin_token, no_provider_listing):
    resp = await client.get("/api/infrastructure/compute/pods", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json() == {"pods": [], "total": 0}


@pytest.mark.parametrize(
    "grants,expected", [([("infrastructure", "configure")], 200), ([("notebooks", "launch")], 403)]
)
@pytest.mark.asyncio
async def test_inventory_follows_the_infrastructure_configure_grant(
    client, session, admin_user, no_provider_listing, grants, expected
):
    headers = await _custom_headers(session, admin_user.organization_id, "infra@test.com", grants)
    resp = await client.get("/api/infrastructure/compute/pods", headers=headers)
    assert resp.status_code == expected


@pytest.mark.asyncio
async def test_inventory_refuses_api_keys_outside_their_scope(client, session, admin_user, no_provider_listing):
    headers = await _key_headers(session, admin_user, ["samples:view"])
    resp = await client.get("/api/infrastructure/compute/pods", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "key_scope_missing"
