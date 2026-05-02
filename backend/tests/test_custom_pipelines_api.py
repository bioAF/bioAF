"""API-level tests for /api/v1/custom-pipelines endpoints + RBAC enforcement."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.experiment import Experiment
from app.models.file import File
from app.models.project import Project
from app.services.auth_service import AuthService
from app.services.event_bus import event_bus


# --- Role/user/token fixtures ---


@pytest_asyncio.fixture(autouse=True)
def _clear_event_bus():
    event_bus.clear()
    yield
    event_bus.clear()


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    user = User(
        email="compbio@test.com",
        password_hash=AuthService.hash_password("compbiopass123"),
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

    user = User(
        email="bench@test.com",
        password_hash=AuthService.hash_password("benchpass123"),
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


# --- Environment/launch fixtures ---


@pytest_asyncio.fixture
async def ready_env_version(session, admin_user):
    env = Environment(
        name="API Pipeline Env",
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
        definition_content="name: pipeline\nchannels: [conda-forge]\ndependencies: [python=3.11]\n",
        image_uri="us-central1-docker.pkg.dev/test/bioaf/pipeline-api:v1",
        created_by_user_id=admin_user.id,
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest_asyncio.fixture
async def project(session, admin_user):
    p = Project(
        organization_id=admin_user.organization_id,
        name="API Launch Project",
        owner_user_id=admin_user.id,
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest_asyncio.fixture
async def experiment(session, admin_user, project):
    exp = Experiment(
        organization_id=admin_user.organization_id,
        project_id=project.id,
        name="API Launch Experiment",
        owner_user_id=admin_user.id,
        status="fastq_uploaded",
    )
    session.add(exp)
    await session.flush()
    await session.commit()
    return exp


@pytest_asyncio.fixture
async def input_files(session, admin_user, project, experiment):
    f1 = File(
        organization_id=admin_user.organization_id,
        gcs_uri="gs://bioaf-raw-test/uploads/api_R1.fastq.gz",
        filename="api_R1.fastq.gz",
        file_type="fastq",
        project_id=project.id,
        experiment_id=experiment.id,
        source_type="upload",
    )
    session.add(f1)
    await session.flush()
    await session.commit()
    return [f1]


@pytest_asyncio.fixture
async def platform_results_bucket(session):
    await session.execute(
        sa_text(
            "INSERT INTO platform_config (key, value) VALUES ('results_bucket_name', 'bioaf-results-api-test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()


def _mock_compute_adapter():
    captured: dict = {}

    async def capture_submit(job_spec):
        captured["job_spec"] = job_spec
        return {
            "job_id": "bioaf-pipeline-api-1",
            "namespace": "bioaf-pipelines",
            "status": "queued",
            "estimated_cost": {"estimated_cost_usd": 0.10},
        }

    adapter = MagicMock()
    adapter.submit_job = AsyncMock(side_effect=capture_submit)
    return adapter


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- Auth requirement ---


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(client):
    response = await client.get("/api/v1/custom-pipelines")
    assert response.status_code == 401


# --- List + Create ---


@pytest.mark.asyncio
async def test_list_empty(client, admin_token):
    response = await client.get("/api/v1/custom-pipelines", headers=_bearer(admin_token))
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_admin_can_create_pipeline(client, admin_token):
    response = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Admin Pipeline", "description": "by admin"},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Admin Pipeline"
    assert body["description"] == "by admin"
    assert body["pipeline_key"] == "admin-pipeline"
    assert body["id"] > 0


@pytest.mark.asyncio
async def test_comp_bio_can_create_pipeline(client, comp_bio_token):
    response = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Comp Bio Pipeline"},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Comp Bio Pipeline"


@pytest.mark.asyncio
async def test_bench_cannot_create_pipeline(client, bench_token):
    response = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Bench Pipeline"},
        headers=_bearer(bench_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_create_pipeline(client, viewer_token):
    response = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Viewer Pipeline"},
        headers=_bearer(viewer_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_list_pipelines(client, admin_token, viewer_token):
    await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Public Pipeline"},
        headers=_bearer(admin_token),
    )
    response = await client.get("/api/v1/custom-pipelines", headers=_bearer(viewer_token))
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_create_pipeline_invalid_name(client, admin_token):
    response = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": ""},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 422


# --- Get / Update / Delete ---


@pytest.mark.asyncio
async def test_get_pipeline_detail(client, admin_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Detail Pipeline"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/custom-pipelines/{pipeline_id}",
        headers=_bearer(admin_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == pipeline_id
    assert body["name"] == "Detail Pipeline"
    assert body["versions"] == []


@pytest.mark.asyncio
async def test_get_pipeline_not_found(client, admin_token):
    response = await client.get("/api/v1/custom-pipelines/99999", headers=_bearer(admin_token))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_pipeline(client, admin_token, comp_bio_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Original"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/custom-pipelines/{pipeline_id}",
        json={"name": "Renamed", "description": "Updated"},
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["description"] == "Updated"


@pytest.mark.asyncio
async def test_bench_cannot_update_pipeline(client, admin_token, bench_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "ReadOnly"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/custom-pipelines/{pipeline_id}",
        json={"name": "Hijack"},
        headers=_bearer(bench_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_pipeline_not_found(client, admin_token):
    response = await client.put(
        "/api/v1/custom-pipelines/99999",
        json={"name": "Whatever"},
        headers=_bearer(admin_token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_delete_pipeline(client, admin_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Trash"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/custom-pipelines/{pipeline_id}",
        headers=_bearer(admin_token),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_comp_bio_cannot_delete_pipeline(client, admin_token, comp_bio_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "NotForDelete"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/custom-pipelines/{pipeline_id}",
        headers=_bearer(comp_bio_token),
    )
    assert response.status_code == 403


# --- Versions ---


@pytest.mark.asyncio
async def test_create_version(client, admin_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Versioned"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print('hi')",
            "entrypoint_command": "python script.py",
            "environment_version_id": ready_env_version.id,
            "variables": [
                {
                    "variable_name": "threads",
                    "default_value": "4",
                    "variable_type": "number",
                    "is_required": True,
                }
            ],
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version_number"] == 1
    assert body["code_source_type"] == "inline"
    assert body["status"] == "active"
    assert body["version_trigger"] == "user"
    assert len(body["variables"]) == 1
    assert body["variables"][0]["variable_name"] == "threads"


@pytest.mark.asyncio
async def test_create_version_invalid_source(client, admin_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "BadVersion"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "github_repo",
            "entrypoint_command": "bash run.sh",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 400
    assert "github_repo_id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_bench_cannot_create_version(client, admin_token, bench_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "Locked"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python run.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(bench_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_version(client, admin_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "GetVersion"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python run.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    response = await client.get(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions/{version_id}",
        headers=_bearer(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["id"] == version_id


@pytest.mark.asyncio
async def test_get_version_not_found(client, admin_token):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "NoVersion"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions/99999",
        headers=_bearer(admin_token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deprecate_version(client, admin_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "ToDeprecate"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python run.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions/{version_id}/deprecate",
        headers=_bearer(admin_token),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "deprecated"}

    detail = await client.get(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions/{version_id}",
        headers=_bearer(admin_token),
    )
    assert detail.json()["status"] == "deprecated"


@pytest.mark.asyncio
async def test_bench_cannot_deprecate_version(client, admin_token, bench_token, ready_env_version):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "BenchDeprecate"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python run.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions/{version_id}/deprecate",
        headers=_bearer(bench_token),
    )
    assert response.status_code == 403


# --- Launch ---


@pytest.mark.asyncio
async def test_admin_can_launch(
    client,
    admin_token,
    ready_env_version,
    experiment,
    input_files,
    platform_results_bucket,
):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "LaunchPipeline"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python /code/script.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    adapter = _mock_compute_adapter()
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        response = await client.post(
            f"/api/v1/custom-pipelines/{pipeline_id}/launch",
            json={
                "version_id": version_id,
                "experiment_id": experiment.id,
                "input_file_ids": [f.id for f in input_files],
            },
            headers=_bearer(admin_token),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["pipeline_name"] == "LaunchPipeline"
    assert body["pipeline_version"] == "1"


@pytest.mark.asyncio
async def test_bench_can_launch(
    client,
    admin_token,
    bench_token,
    ready_env_version,
    experiment,
    input_files,
    platform_results_bucket,
):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "BenchLaunch"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python /code/script.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    adapter = _mock_compute_adapter()
    with (
        patch("app.services.custom_pipeline_service.get_compute_adapter", return_value=adapter),
        patch("app.services.experiment_service.ExperimentService.update_status", new_callable=AsyncMock),
    ):
        response = await client.post(
            f"/api/v1/custom-pipelines/{pipeline_id}/launch",
            json={
                "version_id": version_id,
                "experiment_id": experiment.id,
                "input_file_ids": [f.id for f in input_files],
            },
            headers=_bearer(bench_token),
        )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_viewer_cannot_launch(
    client,
    admin_token,
    viewer_token,
    ready_env_version,
    experiment,
    input_files,
):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "NoViewerLaunch"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    version_resp = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/versions",
        json={
            "code_source_type": "inline",
            "code_content": "print()",
            "entrypoint_command": "python /code/script.py",
            "environment_version_id": ready_env_version.id,
        },
        headers=_bearer(admin_token),
    )
    version_id = version_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/launch",
        json={
            "version_id": version_id,
            "experiment_id": experiment.id,
            "input_file_ids": [f.id for f in input_files],
        },
        headers=_bearer(viewer_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_launch_pipeline_not_found(client, admin_token):
    response = await client.post(
        "/api/v1/custom-pipelines/99999/launch",
        json={
            "version_id": 1,
            "experiment_id": 1,
            "input_file_ids": [],
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_launch_invalid_version(
    client,
    admin_token,
    ready_env_version,
    experiment,
    input_files,
):
    create_resp = await client.post(
        "/api/v1/custom-pipelines",
        json={"name": "BadLaunch"},
        headers=_bearer(admin_token),
    )
    pipeline_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/custom-pipelines/{pipeline_id}/launch",
        json={
            "version_id": 99999,
            "experiment_id": experiment.id,
            "input_file_ids": [f.id for f in input_files],
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 400
