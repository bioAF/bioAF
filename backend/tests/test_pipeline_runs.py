import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio_runs@test.com",
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
async def experiment(session, admin_user):
    from app.models.experiment import Experiment

    exp = Experiment(
        organization_id=admin_user.organization_id,
        name="Test Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def samples(session, experiment):
    from app.models.sample import Sample

    sample_list = []
    for i in range(3):
        s = Sample(
            experiment_id=experiment.id,
            sample_id_unique=f"SAMPLE_{i + 1}",
            organism="Homo sapiens",
            tissue_type="PBMC",
        )
        session.add(s)
        sample_list.append(s)
    await session.flush()
    await session.commit()
    return sample_list


@pytest_asyncio.fixture
async def initialized_catalog(client, admin_token):
    """Ensure pipeline catalog is initialized."""
    await client.get("/api/pipelines", headers={"Authorization": f"Bearer {admin_token}"})


@pytest_asyncio.fixture
async def pipeline_run(session, admin_user, experiment, samples, initialized_catalog, client, admin_token):
    """Create a pipeline run via API."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {"aligner": "cellranger"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        return response.json()


@pytest.mark.asyncio
async def test_launch_run(client, admin_token, experiment, samples, initialized_catalog):
    """Launch a pipeline run creates record, links samples, updates experiment."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ) as mock_status,
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [s.id for s in samples],
                "parameters": {"aligner": "cellranger", "genome": "GRCh38"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["pipeline_name"] == "nf-core/scrnaseq"
        assert data["status"] in ("running", "pending", "failed")
        assert data["parameters"]["aligner"] == "cellranger"
        # Experiment status should have been updated
        mock_status.assert_called_once()


@pytest.mark.asyncio
async def test_launch_run_validates_experiment(client, admin_token, initialized_catalog):
    """Launch fails if experiment doesn't exist."""
    response = await client.post(
        "/api/pipeline-runs",
        json={
            "pipeline_key": "nf-core/scrnaseq",
            "experiment_id": 99999,
            "parameters": {},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_validates_pipeline(client, admin_token, experiment, initialized_catalog):
    """Launch fails if pipeline doesn't exist."""
    response = await client.post(
        "/api/pipeline-runs",
        json={
            "pipeline_key": "nonexistent/pipeline",
            "experiment_id": experiment.id,
            "parameters": {},
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_validates_samples(client, admin_token, experiment, samples, initialized_catalog):
    """Launch fails if sample IDs don't belong to the experiment."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="12345",
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "sample_ids": [99999],
                "parameters": {},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_launch_run_creates_audit_entry(client, admin_token, experiment, samples, initialized_catalog, session):
    """Launch creates an audit log entry."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "pipeline_run",
            AuditLog.action == "launch",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_cancel_run(client, admin_token, pipeline_run):
    """Cancel a running pipeline."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_creates_audit_entry(client, admin_token, pipeline_run, session):
    """Cancel writes an audit log entry."""
    with patch(
        "app.services.slurm_service.SlurmService._run_ssh_command",
        new_callable=AsyncMock,
        return_value="",
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "pipeline_run",
            AuditLog.action == "cancel",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_list_runs(client, admin_token, pipeline_run):
    """List pipeline runs returns runs."""
    response = await client.get(
        "/api/pipeline-runs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["runs"]) >= 1


@pytest.mark.asyncio
async def test_list_runs_filter_by_experiment(client, admin_token, pipeline_run, experiment):
    """List runs with experiment filter."""
    response = await client.get(
        f"/api/pipeline-runs?experiment_id={experiment.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(r["experiment"]["id"] == experiment.id for r in data["runs"])


@pytest.mark.asyncio
async def test_get_run_detail(client, admin_token, pipeline_run):
    """Get run detail with processes and samples."""
    response = await client.get(
        f"/api/pipeline-runs/{pipeline_run['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == pipeline_run["id"]
    assert "processes" in data
    assert "samples" in data


@pytest.mark.asyncio
async def test_reproduce_run(client, admin_token, pipeline_run):
    """Reproduce creates a new run with same params."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="67890",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        response = await client.post(
            f"/api/pipeline-runs/{pipeline_run['id']}/reproduce",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] != pipeline_run["id"]
        assert data["resume_from_run_id"] == pipeline_run["id"]


@pytest.mark.asyncio
async def test_provenance_export(client, admin_token, pipeline_run):
    """Provenance export returns expected structure."""
    response = await client.get(
        f"/api/pipeline-runs/{pipeline_run['id']}/provenance",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "pipeline_name" in data
    assert "parameters" in data
    assert "samples" in data
    assert "experiment" in data


@pytest.mark.asyncio
async def test_compare_runs(client, admin_token, experiment, samples, initialized_catalog):
    """Compare runs shows parameter diffs."""
    with (
        patch(
            "app.services.slurm_service.SlurmService._run_ssh_command",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch(
            "app.services.experiment_service.ExperimentService.update_status",
            new_callable=AsyncMock,
        ),
    ):
        # Create two runs with different params
        r1 = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {"aligner": "star"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        r2 = await client.post(
            "/api/pipeline-runs",
            json={
                "pipeline_key": "nf-core/scrnaseq",
                "experiment_id": experiment.id,
                "parameters": {"aligner": "cellranger"},
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    response = await client.post(
        "/api/pipeline-runs/compare",
        json={"run_ids": [r1.json()["id"], r2.json()["id"]]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    assert "aligner" in data["parameter_diffs"]


@pytest.mark.asyncio
async def test_viewer_cannot_access_runs(client, viewer_token):
    """Viewer users cannot access pipeline run endpoints."""
    response = await client.get(
        "/api/pipeline-runs",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
