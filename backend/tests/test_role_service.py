"""Tests for role_service and the require_permission dependency."""

import pytest
from httpx import AsyncClient

from app.services import role_service


@pytest.mark.asyncio
async def test_seed_builtin_roles_creates_four_roles(session, admin_user):
    """seed_builtin_roles creates admin, comp_bio, bench, viewer for an org."""
    role_map = admin_user._test_role_map
    assert set(role_map.keys()) == {"admin", "comp_bio", "bench", "viewer"}
    for role_id in role_map.values():
        role = await role_service.get_role_by_id(session, role_id)
        assert role is not None
        assert role.is_system is True


@pytest.mark.asyncio
async def test_has_permission_admin_has_all(session, admin_user):
    """Admin role has all resource/action pairs."""
    role_map = admin_user._test_role_map
    admin_role_id = role_map["admin"]
    assert await role_service.has_permission(session, admin_role_id, "experiments", "create")
    assert await role_service.has_permission(session, admin_role_id, "users", "deactivate")
    assert await role_service.has_permission(session, admin_role_id, "infrastructure", "deploy")


@pytest.mark.asyncio
async def test_has_permission_viewer_limited(session, admin_user):
    """Viewer role has only view permissions on a subset of resources."""
    role_map = admin_user._test_role_map
    viewer_role_id = role_map["viewer"]
    assert await role_service.has_permission(session, viewer_role_id, "experiments", "view")
    assert not await role_service.has_permission(session, viewer_role_id, "experiments", "create")
    assert not await role_service.has_permission(session, viewer_role_id, "users", "view")
    assert not await role_service.has_permission(session, viewer_role_id, "infrastructure", "configure")


@pytest.mark.asyncio
async def test_has_permission_bench_cannot_launch_notebooks(session, admin_user):
    """Bench role cannot launch notebooks (comp_bio+ only)."""
    role_map = admin_user._test_role_map
    bench_role_id = role_map["bench"]
    assert await role_service.has_permission(session, bench_role_id, "experiments", "create")
    assert not await role_service.has_permission(session, bench_role_id, "notebooks", "launch")


@pytest.mark.asyncio
async def test_permission_cache_invalidation(session, admin_user):
    """Invalidating the cache forces a fresh DB read."""
    role_map = admin_user._test_role_map
    admin_role_id = role_map["admin"]

    # Warm the cache
    await role_service.get_permissions_for_role(session, admin_role_id)
    assert admin_role_id in role_service._permission_cache

    # Invalidate specific role
    role_service.invalidate_cache(admin_role_id)
    assert admin_role_id not in role_service._permission_cache

    # Re-warm and invalidate all
    await role_service.get_permissions_for_role(session, admin_role_id)
    role_service.invalidate_cache()
    assert len(role_service._permission_cache) == 0


@pytest.mark.asyncio
async def test_create_custom_role(session, admin_user):
    """Can create a custom (non-system) role with specific permissions."""
    org_id = admin_user.organization_id
    role = await role_service.create_role(
        session,
        org_id,
        name="data_steward",
        description="Data governance role",
        permissions=[("experiments", "view"), ("samples", "view"), ("samples", "edit")],
    )
    await session.flush()

    assert role.is_system is False
    assert role.name == "data_steward"

    perms = await role_service.get_role_permissions(session, role.id)
    assert len(perms) == 3
    assert {"resource": "samples", "action": "edit"} in perms


@pytest.mark.asyncio
async def test_set_permissions_replaces_existing(session, admin_user):
    """set_permissions replaces all permissions for a role."""
    org_id = admin_user.organization_id
    role = await role_service.create_role(session, org_id, name="temp_role", permissions=[("experiments", "view")])
    await session.flush()

    await role_service.set_permissions(session, role.id, [("samples", "create"), ("files", "upload")])
    perms = await role_service.get_role_permissions(session, role.id)
    assert len(perms) == 2
    resources = {p["resource"] for p in perms}
    assert "experiments" not in resources
    assert "samples" in resources


@pytest.mark.asyncio
async def test_list_roles_returns_org_roles(session, admin_user):
    """list_roles returns all roles for an organization."""
    org_id = admin_user.organization_id
    roles = await role_service.list_roles(session, org_id)
    role_names = {r.name for r in roles}
    assert {"admin", "comp_bio", "bench", "viewer"}.issubset(role_names)


@pytest.mark.asyncio
async def test_delete_role(session, admin_user):
    """Can delete a non-system role."""
    org_id = admin_user.organization_id
    role = await role_service.create_role(session, org_id, name="disposable")
    await session.flush()

    await role_service.delete_role(session, role)
    await session.flush()

    assert await role_service.get_role_by_id(session, role.id) is None


@pytest.mark.asyncio
async def test_get_role_by_name(session, admin_user):
    """get_role_by_name finds a role by org and name."""
    org_id = admin_user.organization_id
    role = await role_service.get_role_by_name(session, org_id, "admin")
    assert role is not None
    assert role.name == "admin"
    assert role.organization_id == org_id


# --- API-level permission tests ---


@pytest.mark.asyncio
async def test_require_permission_allows_authorized(client: AsyncClient, admin_token: str):
    """Admin can access an endpoint requiring experiments.view."""
    response = await client.get(
        "/api/experiments",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_require_permission_denies_unauthorized(client: AsyncClient, viewer_token: str):
    """Viewer cannot access an endpoint requiring experiments.create."""
    response = await client.post(
        "/api/experiments",
        json={"name": "test", "experiment_type": "scrna"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
