"""Tests for SSH connection command endpoints."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.experiment import Experiment
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.services.auth_service import AuthService


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
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest_asyncio.fixture
async def bench_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("benchpass123")
    user = User(
        email="bench@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["bench"],
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
async def running_pipeline_run(session, admin_user):
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
async def running_notebook_session(session, admin_user):
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
    assert data["namespace"] == "bioaf-interactive"
