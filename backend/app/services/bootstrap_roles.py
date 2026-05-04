from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role, RolePermission

ALL_RESOURCES_ACTIONS: dict[str, list[str]] = {
    "experiments": ["view", "create", "edit", "delete", "change_status", "upload"],
    "samples": ["view", "create", "edit", "delete", "change_status"],
    "pipelines": ["view", "create", "edit", "delete", "launch", "cancel", "configure", "change_status"],
    "custom_pipelines": ["view", "create", "edit", "launch", "delete"],
    "notebooks": ["view", "create", "edit", "launch", "stop"],
    "work_nodes": ["view", "launch", "stop", "configure"],
    "environments": ["view", "create", "build", "delete"],
    "files": ["view", "upload", "download", "edit", "delete"],
    "projects": ["view", "create", "edit", "delete"],
    "users": ["view", "invite", "edit_role", "deactivate", "delete"],
    "infrastructure": ["view", "create", "edit", "configure", "deploy", "change_status", "build"],
    "audit_log": ["view"],
    "notifications": ["view", "configure"],
    "backups": ["view", "create", "restore"],
    "cost_center": ["view", "configure_budgets"],
    "roles": ["view", "create", "edit", "delete"],
    "quotas": ["view", "configure"],
    "settings": ["view", "configure"],
    "references": ["view", "upload"],
}

BUILTIN_ROLES: dict[str, tuple[str, dict[str, list[str]]]] = {
    "admin": (
        "Full access to all resources",
        ALL_RESOURCES_ACTIONS,
    ),
    "comp_bio": (
        "Computational biology - full data access, view-only admin",
        {
            "experiments": ["view", "create", "edit", "delete", "change_status", "upload"],
            "samples": ["view", "create", "edit", "delete", "change_status"],
            "pipelines": ["view", "create", "edit", "delete", "launch", "cancel", "configure", "change_status"],
            "custom_pipelines": ["view", "create", "edit", "launch"],
            "notebooks": ["view", "create", "edit", "launch", "stop"],
            "work_nodes": ["view", "launch", "stop"],
            "environments": ["view", "create", "build", "delete"],
            "files": ["view", "upload", "download", "edit", "delete"],
            "projects": ["view", "create", "edit", "delete"],
            "users": ["view"],
            "infrastructure": ["view"],
            "audit_log": ["view"],
            "cost_center": ["view"],
            "references": ["view", "upload"],
        },
    ),
    "bench": (
        "Bench scientist - create and edit experiments and samples",
        {
            "experiments": ["view", "create", "edit", "upload"],
            "samples": ["view", "create", "edit"],
            "pipelines": ["view"],
            "custom_pipelines": ["view", "launch"],
            "notebooks": ["view"],
            "environments": ["view"],
            "files": ["view", "upload"],
            "projects": ["view"],
            "references": ["view"],
        },
    ),
    "viewer": (
        "Read-only access to data",
        {
            "experiments": ["view"],
            "samples": ["view"],
            "custom_pipelines": ["view"],
            "environments": ["view"],
            "files": ["view"],
            "projects": ["view"],
            "references": ["view"],
        },
    ),
}


async def seed_builtin_roles(session: AsyncSession, org_id: int) -> dict[str, int]:
    """Seed built-in roles for an organization. Returns {role_name: role_id} map."""
    role_map: dict[str, int] = {}

    for role_name, (description, perm_map) in BUILTIN_ROLES.items():
        role = Role(
            name=role_name,
            description=description,
            organization_id=org_id,
            is_system=True,
        )
        session.add(role)
        await session.flush()
        role_map[role_name] = role.id

        for resource, actions in perm_map.items():
            for action in actions:
                session.add(RolePermission(role_id=role.id, resource=resource, action=action))

    await session.flush()
    return role_map
