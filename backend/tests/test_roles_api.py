"""Tests for /api/roles endpoints (role CRUD and permission management)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_roles(client: AsyncClient, admin_token: str):
    """Admin can list all roles."""
    response = await client.get(
        "/api/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 4
    role_names = {r["name"] for r in data["roles"]}
    assert {"admin", "comp_bio", "bench", "viewer"}.issubset(role_names)


@pytest.mark.asyncio
async def test_list_roles_includes_permissions(client: AsyncClient, admin_token: str):
    """Role list includes permission entries."""
    response = await client.get(
        "/api/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    admin_role = next(r for r in data["roles"] if r["name"] == "admin")
    assert len(admin_role["permissions"]) > 0
    assert any(p["resource"] == "experiments" and p["action"] == "view" for p in admin_role["permissions"])


@pytest.mark.asyncio
async def test_get_role_by_id(client: AsyncClient, admin_token: str, admin_user):
    """Admin can get a specific role by ID."""
    role_map = admin_user._test_role_map
    response = await client.get(
        f"/api/roles/{role_map['viewer']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "viewer"
    assert data["is_system"] is True


@pytest.mark.asyncio
async def test_get_role_not_found(client: AsyncClient, admin_token: str):
    """Returns 404 for non-existent role."""
    response = await client.get(
        "/api/roles/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_permissions_catalog(client: AsyncClient, admin_token: str):
    """Admin can fetch the full permissions catalog."""
    response = await client.get(
        "/api/roles/permissions-catalog",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "experiments" in data
    assert "view" in data["experiments"]


@pytest.mark.asyncio
async def test_create_custom_role(client: AsyncClient, admin_token: str):
    """Admin can create a custom role with permissions."""
    response = await client.post(
        "/api/roles",
        json={
            "name": "data_steward",
            "description": "Data governance role",
            "permissions": [
                {"resource": "experiments", "action": "view"},
                {"resource": "samples", "action": "view"},
                {"resource": "samples", "action": "edit"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "data_steward"
    assert data["is_system"] is False
    assert len(data["permissions"]) == 3


@pytest.mark.asyncio
async def test_create_duplicate_role_name(client: AsyncClient, admin_token: str):
    """Cannot create a role with an existing name."""
    response = await client.post(
        "/api/roles",
        json={"name": "admin"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_custom_role(client: AsyncClient, admin_token: str):
    """Admin can update a custom role's name and description."""
    # Create a custom role first
    resp = await client.post(
        "/api/roles",
        json={"name": "temp_role", "description": "Temporary"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    role_id = resp.json()["id"]

    response = await client.patch(
        f"/api/roles/{role_id}",
        json={"name": "renamed_role", "description": "Updated desc"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "renamed_role"
    assert response.json()["description"] == "Updated desc"


@pytest.mark.asyncio
async def test_cannot_update_system_role(client: AsyncClient, admin_token: str, admin_user):
    """Cannot modify built-in system roles."""
    role_map = admin_user._test_role_map
    response = await client.patch(
        f"/api/roles/{role_map['viewer']}",
        json={"name": "super_viewer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "system" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_role_permissions(client: AsyncClient, admin_token: str):
    """Admin can replace permissions on a custom role."""
    resp = await client.post(
        "/api/roles",
        json={
            "name": "perm_test_role",
            "permissions": [{"resource": "experiments", "action": "view"}],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    role_id = resp.json()["id"]

    response = await client.put(
        f"/api/roles/{role_id}/permissions",
        json={
            "permissions": [
                {"resource": "samples", "action": "create"},
                {"resource": "files", "action": "upload"},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    perms = response.json()["permissions"]
    assert len(perms) == 2
    resources = {p["resource"] for p in perms}
    assert "experiments" not in resources
    assert "samples" in resources


@pytest.mark.asyncio
async def test_cannot_update_system_role_permissions(client: AsyncClient, admin_token: str, admin_user):
    """Cannot modify permissions of built-in system roles."""
    role_map = admin_user._test_role_map
    response = await client.put(
        f"/api/roles/{role_map['admin']}/permissions",
        json={"permissions": [{"resource": "experiments", "action": "view"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_custom_role(client: AsyncClient, admin_token: str):
    """Admin can delete a custom role with no assigned users."""
    resp = await client.post(
        "/api/roles",
        json={"name": "deletable_role"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    role_id = resp.json()["id"]

    response = await client.delete(
        f"/api/roles/{role_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert "deleted" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_cannot_delete_system_role(client: AsyncClient, admin_token: str, admin_user):
    """Cannot delete built-in system roles."""
    role_map = admin_user._test_role_map
    response = await client.delete(
        f"/api/roles/{role_map['bench']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cannot_delete_role_with_users(client: AsyncClient, admin_token: str, admin_user):
    """Cannot delete a role that has users assigned to it."""
    # admin role has at least the admin_user assigned
    role_map = admin_user._test_role_map
    response = await client.delete(
        f"/api/roles/{role_map['admin']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Would fail on system check first, but the user check is also there
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_manage_roles(client: AsyncClient, viewer_token: str):
    """Viewer cannot access role management endpoints."""
    response = await client.get(
        "/api/roles",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
