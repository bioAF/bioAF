import pytest
import pytest_asyncio

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
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


# --- Environment CRUD tests ---


@pytest.mark.asyncio
async def test_list_environments_empty(client, admin_token):
    """List environments returns empty when none created."""
    response = await client.get(
        "/api/v1/environments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["environments"] == []


@pytest.mark.asyncio
async def test_create_environment(client, admin_token):
    """Admin can create an environment."""
    response = await client.post(
        "/api/v1/environments",
        json={"name": "seurat-gpu", "description": "GPU-accelerated Seurat", "visibility": "organization"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "seurat-gpu"
    assert data["description"] == "GPU-accelerated Seurat"
    assert data["visibility"] == "organization"
    assert data["version_count"] == 0
    assert data["latest_version"] is None
    assert data["id"] > 0


@pytest.mark.asyncio
async def test_create_environment_duplicate_name(client, admin_token):
    """Cannot create two environments with the same name in one org."""
    await client.post(
        "/api/v1/environments",
        json={"name": "my-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response = await client.post(
        "/api/v1/environments",
        json={"name": "my-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_environment_detail(client, admin_token):
    """Get environment by ID with versions list."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "test-env", "description": "Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.get(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-env"
    assert data["versions"] == []


@pytest.mark.asyncio
async def test_get_environment_not_found(client, admin_token):
    response = await client.get(
        "/api/v1/environments/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_environment(client, admin_token):
    """Update environment metadata."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "updatable-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/v1/environments/{env_id}",
        json={"description": "Updated description", "visibility": "organization"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "Updated description"
    assert data["visibility"] == "organization"


@pytest.mark.asyncio
async def test_delete_environment(client, admin_token):
    """Admin can delete an environment."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "deletable-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Verify deleted
    get_resp = await client.get(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_resp.status_code == 404


# --- Permission tests ---


@pytest.mark.asyncio
async def test_viewer_can_list_environments(client, viewer_token):
    """Viewers can list environments."""
    response = await client.get(
        "/api/v1/environments",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_create_environment(client, viewer_token):
    """Viewers cannot create environments."""
    response = await client.post(
        "/api/v1/environments",
        json={"name": "viewer-env"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_delete_environment(client, admin_token, viewer_token):
    """Viewers cannot delete environments."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "protected-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.delete(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_comp_bio_can_create_environment(client, comp_bio_token):
    """comp_bio role can create environments."""
    response = await client.post(
        "/api/v1/environments",
        json={"name": "compbio-env", "description": "For comp bio"},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_bench_cannot_create_environment(client, bench_token):
    """bench role cannot create environments (only view)."""
    response = await client.post(
        "/api/v1/environments",
        json={"name": "bench-env"},
        headers={"Authorization": f"Bearer {bench_token}"},
    )
    assert response.status_code == 403


# --- Version tests ---


@pytest.mark.asyncio
async def test_create_version(client, admin_token):
    """Create a version for an environment."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "versioned-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={
            "definition_format": "dockerfile",
            "definition_content": "FROM python:3.11\nRUN pip install scanpy",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["version_number"] == 1
    assert data["status"] == "draft"
    assert data["definition_format"] == "dockerfile"
    assert "scanpy" in data["definition_content"]


@pytest.mark.asyncio
async def test_version_auto_increment(client, admin_token):
    """Version numbers auto-increment per environment."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "multi-version-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    # Create version 1
    v1 = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert v1.json()["version_number"] == 1

    # Create version 2
    v2 = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.12"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert v2.json()["version_number"] == 2

    # Create version 3
    v3 = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "conda", "definition_content": "name: myenv\ndependencies:\n  - python=3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert v3.json()["version_number"] == 3


@pytest.mark.asyncio
async def test_get_version_detail(client, admin_token):
    """Get a specific version by ID."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "detail-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    ver_resp = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM ubuntu:22.04"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ver_id = ver_resp.json()["id"]

    response = await client.get(
        f"/api/v1/environments/{env_id}/versions/{ver_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version_number"] == 1
    assert data["definition_content"] == "FROM ubuntu:22.04"
    assert data["build_id"] is None
    assert data["image_uri"] is None


@pytest.mark.asyncio
async def test_environment_detail_includes_versions(client, admin_token):
    """Environment detail endpoint includes version summaries."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "with-versions"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.12"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    assert len(data["versions"]) == 2
    # Sorted descending by version_number
    assert data["versions"][0]["version_number"] == 2
    assert data["versions"][1]["version_number"] == 1


@pytest.mark.asyncio
async def test_list_environments_shows_version_count(client, admin_token):
    """List endpoint includes version count and latest version."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "counted-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/v1/environments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    env = next(e for e in data["environments"] if e["name"] == "counted-env")
    assert env["version_count"] == 1
    assert env["latest_version"]["version_number"] == 1
    assert env["latest_version"]["status"] == "draft"


@pytest.mark.asyncio
async def test_viewer_cannot_create_version(client, admin_token, viewer_token):
    """Viewers cannot create versions (requires environments.create)."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "viewer-test-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_trigger_build(client, admin_token, viewer_token):
    """Viewers cannot trigger builds (requires environments.build)."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "build-test-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    ver_resp = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ver_id = ver_resp.json()["id"]

    response = await client.post(
        f"/api/v1/environments/{env_id}/versions/{ver_id}/build",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_invalid_definition_format(client, admin_token):
    """Reject invalid definition_format values."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "invalid-fmt-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "invalid", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_conda_version_creation(client, admin_token):
    """Create a conda-format version."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "conda-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    conda_yml = "name: bioaf-conda\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n  - scanpy\n"
    response = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "conda", "definition_content": conda_yml},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["definition_format"] == "conda"
    assert "scanpy" in data["definition_content"]


@pytest.mark.asyncio
async def test_delete_environment_cascades_versions(client, admin_token):
    """Deleting an environment removes its versions."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "cascade-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    ver_resp = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ver_id = ver_resp.json()["id"]

    # Delete environment
    response = await client.delete(
        f"/api/v1/environments/{env_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204

    # Version should also be gone
    ver_check = await client.get(
        f"/api/v1/environments/{env_id}/versions/{ver_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ver_check.status_code == 404


@pytest.mark.asyncio
async def test_build_logs_endpoint(client, admin_token):
    """Build logs endpoint returns status for a version."""
    create_resp = await client.post(
        "/api/v1/environments",
        json={"name": "logs-env"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    env_id = create_resp.json()["id"]

    ver_resp = await client.post(
        f"/api/v1/environments/{env_id}/versions",
        json={"definition_format": "dockerfile", "definition_content": "FROM python:3.11"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ver_id = ver_resp.json()["id"]

    response = await client.get(
        f"/api/v1/environments/{env_id}/versions/{ver_id}/logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["build_id"] is None
