import time
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role, RolePermission

# In-memory permission cache: {role_id: (expires_at, frozenset[("resource", "action")])}
_permission_cache: dict[int, tuple[float, frozenset[tuple[str, str]]]] = {}
_CACHE_TTL = 60.0  # seconds


def invalidate_cache(role_id: int | None = None) -> None:
    """Clear the permission cache for a specific role or all roles."""
    if role_id is None:
        _permission_cache.clear()
    else:
        _permission_cache.pop(role_id, None)


async def get_permissions_for_role(session: AsyncSession, role_id: int) -> frozenset[tuple[str, str]]:
    """Return the set of (resource, action) tuples for a role, using cache."""
    now = time.monotonic()
    cached = _permission_cache.get(role_id)
    if cached and cached[0] > now:
        return cached[1]

    result = await session.execute(
        select(RolePermission.resource, RolePermission.action).where(RolePermission.role_id == role_id)
    )
    perms = frozenset(result.fetchall())
    _permission_cache[role_id] = (now + _CACHE_TTL, perms)
    return perms


async def has_permission(session: AsyncSession, role_id: int, resource: str, action: str) -> bool:
    """Check if a role has a specific permission."""
    perms = await get_permissions_for_role(session, role_id)
    return (resource, action) in perms


async def get_role_by_id(session: AsyncSession, role_id: int) -> Role | None:
    result = await session.execute(select(Role).where(Role.id == role_id))
    return result.scalar_one_or_none()


async def get_role_by_name(session: AsyncSession, org_id: int, name: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.organization_id == org_id, Role.name == name))
    return result.scalar_one_or_none()


async def list_roles(session: AsyncSession, org_id: int) -> Sequence[Role]:
    result = await session.execute(select(Role).where(Role.organization_id == org_id).order_by(Role.name))
    return result.scalars().all()


async def create_role(
    session: AsyncSession,
    org_id: int,
    name: str,
    description: str | None = None,
    permissions: list[tuple[str, str]] | None = None,
) -> Role:
    role = Role(name=name, description=description, organization_id=org_id, is_system=False)
    session.add(role)
    await session.flush()

    if permissions:
        for resource, action in permissions:
            session.add(RolePermission(role_id=role.id, resource=resource, action=action))
        await session.flush()

    return role


async def update_role(
    session: AsyncSession,
    role: Role,
    name: str | None = None,
    description: str | None = None,
) -> Role:
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    await session.flush()
    return role


async def delete_role(session: AsyncSession, role: Role) -> None:
    await session.delete(role)
    await session.flush()


async def set_permissions(
    session: AsyncSession,
    role_id: int,
    permissions: list[tuple[str, str]],
) -> None:
    """Replace all permissions for a role with the given set."""
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    for resource, action in permissions:
        session.add(RolePermission(role_id=role_id, resource=resource, action=action))
    await session.flush()
    invalidate_cache(role_id)


async def get_role_permissions(session: AsyncSession, role_id: int) -> list[dict[str, str]]:
    """Return permissions as a list of dicts for API responses."""
    result = await session.execute(
        select(RolePermission.resource, RolePermission.action).where(RolePermission.role_id == role_id)
    )
    return [{"resource": r, "action": a} for r, a in result.fetchall()]
