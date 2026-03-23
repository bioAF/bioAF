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


@pytest.mark.asyncio
async def test_list_environments_initializes_defaults(client, admin_token):
    """First call initializes default environments."""
    response = await client.get(
        "/api/environments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2
    names = [e["name"] for e in data["environments"]]
    assert "bioaf-scrna" in names
    assert "bioaf-rstudio" in names


@pytest.mark.asyncio
async def test_list_environments_idempotent(client, admin_token):
    """Calling list twice doesn't duplicate environments."""
    await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})
    response = await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})
    data = response.json()
    names = [e["name"] for e in data["environments"]]
    assert names.count("bioaf-scrna") == 1


@pytest.mark.asyncio
async def test_get_environment_detail(client, admin_token):
    """Get environment detail with packages."""
    # Initialize first
    await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.get(
        "/api/environments/bioaf-scrna",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "bioaf-scrna"
    assert data["env_type"] == "conda"
    assert data["is_default"] is True
    assert len(data["packages"]) > 0


@pytest.mark.asyncio
async def test_get_environment_not_found(client, admin_token):
    response = await client.get(
        "/api/environments/nonexistent",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_viewer_can_list_environments(client, viewer_token):
    """Viewers can view environments."""
    response = await client.get(
        "/api/environments",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_create_environment(client, viewer_token):
    """Viewers cannot create environments."""
    response = await client.post(
        "/api/environments",
        json={"name": "test-env"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_archive_environment(client, viewer_token):
    """Viewers cannot archive environments."""
    response = await client.delete(
        "/api/environments/bioaf-scrna",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cannot_archive_default_environment(client, admin_token):
    """Default environments cannot be archived."""
    # Initialize first
    await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.delete(
        "/api/environments/bioaf-scrna",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_environment_history_empty(client, admin_token):
    """History is empty for new environments."""
    await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.get(
        "/api/environments/bioaf-scrna/history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["changes"] == []


@pytest.mark.asyncio
async def test_environment_packages_list(client, admin_token):
    """Packages endpoint returns installed packages."""
    await client.get("/api/environments", headers={"Authorization": f"Bearer {admin_token}"})

    response = await client.get(
        "/api/environments/bioaf-scrna/packages",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    pkg_names = [p["name"] for p in data["packages"]]
    assert "scanpy" in pkg_names
