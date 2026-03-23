import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

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
async def test_search_packages_requires_auth(client, viewer_token):
    """Viewer cannot search packages."""
    response = await client.get(
        "/api/packages/search?query=scanpy",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_search_packages_comp_bio(client, comp_bio_token):
    """Comp Bio can search packages."""
    with patch(
        "app.services.package_search_service.PackageSearchService.search_packages",
        new_callable=AsyncMock,
        return_value=[
            {
                "name": "scanpy",
                "version": "1.10.0",
                "description": "Single-cell analysis",
                "source": "conda",
                "channel": "conda-forge",
                "homepage": None,
            },
        ],
    ):
        response = await client.get(
            "/api/packages/search?query=scanpy&sources=conda",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "scanpy"
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "scanpy"


@pytest.mark.asyncio
async def test_search_packages_admin(client, admin_token):
    """Admin can search packages."""
    with patch(
        "app.services.package_search_service.PackageSearchService.search_packages",
        new_callable=AsyncMock,
        return_value=[],
    ):
        response = await client.get(
            "/api/packages/search?query=nonexistent",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_install_package_viewer_denied(client, viewer_token):
    """Viewer cannot install packages."""
    response = await client.post(
        "/api/packages/install",
        json={"environment": "bioaf-scrna", "package_name": "test", "source": "conda"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_remove_package_viewer_denied(client, viewer_token):
    """Viewer cannot remove packages."""
    response = await client.post(
        "/api/packages/remove",
        json={"environment": "bioaf-scrna", "package_name": "test", "source": "conda"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dependencies_comp_bio(client, comp_bio_token):
    """Comp Bio can get dependency tree."""
    with patch(
        "app.services.package_search_service.PackageSearchService.get_dependency_tree",
        new_callable=AsyncMock,
        return_value={
            "package": "scanpy",
            "version": "latest",
            "dependencies": [],
            "total_new_packages": 0,
            "estimated_disk_bytes": None,
        },
    ):
        response = await client.get(
            "/api/packages/dependencies?package_name=scanpy&source=conda&environment=bioaf-scrna",
            headers={"Authorization": f"Bearer {comp_bio_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["package"] == "scanpy"
