from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.role import (
    PermissionEntry,
    RoleCreate,
    RoleListResponse,
    RolePermissionsUpdate,
    RoleResponse,
    RoleUpdate,
)
from app.services import role_service
from app.services.audit_service import log_action
from app.services.bootstrap_roles import ALL_RESOURCES_ACTIONS

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _role_response(role, permissions: list[dict[str, str]]) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        organization_id=role.organization_id,
        is_system=role.is_system,
        permissions=[PermissionEntry(**p) for p in permissions],
        created_at=role.created_at,
    )


@router.get("", response_model=RoleListResponse)
async def list_roles(
    current_user: dict = require_permission("roles", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    roles = await role_service.list_roles(session, org_id)

    role_responses = []
    for role in roles:
        perms = await role_service.get_role_permissions(session, role.id)
        role_responses.append(_role_response(role, perms))

    return RoleListResponse(roles=role_responses, total=len(role_responses))


@router.get("/permissions-catalog")
async def permissions_catalog(
    current_user: dict = require_permission("roles", "view"),
):
    """Return the full catalog of available resource/action pairs."""
    return {resource: actions for resource, actions in sorted(ALL_RESOURCES_ACTIONS.items())}


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    current_user: dict = require_permission("roles", "view"),
    session: AsyncSession = Depends(get_session),
):
    role = await role_service.get_role_by_id(session, role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    perms = await role_service.get_role_permissions(session, role.id)
    return _role_response(role, perms)


@router.post("", response_model=RoleResponse)
async def create_role(
    body: RoleCreate,
    current_user: dict = require_permission("roles", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    actor_id = int(current_user["sub"])

    existing = await role_service.get_role_by_name(session, org_id, body.name)
    if existing:
        raise HTTPException(409, "Role with this name already exists")

    perm_tuples = [(p.resource, p.action) for p in body.permissions]
    role = await role_service.create_role(
        session, org_id, name=body.name, description=body.description, permissions=perm_tuples
    )

    await log_action(
        session,
        user_id=actor_id,
        entity_type="role",
        entity_id=role.id,
        action="create",
        details={"role_name": body.name, "permission_count": len(perm_tuples)},
    )
    await session.commit()

    perms = await role_service.get_role_permissions(session, role.id)
    return _role_response(role, perms)


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    current_user: dict = require_permission("roles", "edit"),
    session: AsyncSession = Depends(get_session),
):
    actor_id = int(current_user["sub"])

    role = await role_service.get_role_by_id(session, role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    if role.is_system:
        raise HTTPException(400, "Cannot modify a built-in system role")

    if body.name and body.name != role.name:
        org_id = int(current_user["org_id"])
        existing = await role_service.get_role_by_name(session, org_id, body.name)
        if existing:
            raise HTTPException(409, "Role with this name already exists")

    role = await role_service.update_role(session, role, name=body.name, description=body.description)

    await log_action(
        session,
        user_id=actor_id,
        entity_type="role",
        entity_id=role.id,
        action="update",
        details={"role_name": role.name},
    )
    await session.commit()

    perms = await role_service.get_role_permissions(session, role.id)
    return _role_response(role, perms)


@router.put("/{role_id}/permissions", response_model=RoleResponse)
async def update_role_permissions(
    role_id: int,
    body: RolePermissionsUpdate,
    current_user: dict = require_permission("roles", "edit"),
    session: AsyncSession = Depends(get_session),
):
    actor_id = int(current_user["sub"])

    role = await role_service.get_role_by_id(session, role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    if role.is_system:
        raise HTTPException(400, "Cannot modify permissions of a built-in system role")

    perm_tuples = [(p.resource, p.action) for p in body.permissions]
    await role_service.set_permissions(session, role.id, perm_tuples)

    await log_action(
        session,
        user_id=actor_id,
        entity_type="role",
        entity_id=role.id,
        action="update_permissions",
        details={"role_name": role.name, "permission_count": len(perm_tuples)},
    )
    await session.commit()

    perms = await role_service.get_role_permissions(session, role.id)
    return _role_response(role, perms)


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    current_user: dict = require_permission("roles", "delete"),
    session: AsyncSession = Depends(get_session),
):
    actor_id = int(current_user["sub"])

    role = await role_service.get_role_by_id(session, role_id)
    if not role:
        raise HTTPException(404, "Role not found")

    if role.is_system:
        raise HTTPException(400, "Cannot delete a built-in system role")

    # Check if any users are assigned to this role
    from sqlalchemy import func, select

    from app.models.user import User

    count_result = await session.execute(select(func.count()).select_from(User).where(User.role_id == role_id))
    user_count = count_result.scalar_one()
    if user_count > 0:
        raise HTTPException(400, f"Cannot delete role with {user_count} assigned user(s)")

    role_name = role.name
    await role_service.delete_role(session, role)

    await log_action(
        session,
        user_id=actor_id,
        entity_type="role",
        entity_id=role_id,
        action="delete",
        details={"role_name": role_name},
    )
    await session.commit()

    return {"message": f"Role '{role_name}' deleted"}
