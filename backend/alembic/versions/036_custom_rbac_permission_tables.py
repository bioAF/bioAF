"""Add roles, role_permissions tables; migrate users.role to users.role_id.

Revision ID: 036
Revises: 035
Create Date: 2026-03-23
"""

import sqlalchemy as sa
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

# --- Default permission matrix ---

ALL_RESOURCES_ACTIONS = {
    "experiments": ["view", "create", "edit", "delete", "change_status", "upload"],
    "samples": ["view", "create", "edit", "delete", "change_status"],
    "pipelines": ["view", "create", "edit", "delete", "launch", "cancel", "configure", "change_status"],
    "notebooks": ["view", "create", "edit", "launch", "stop"],
    "work_nodes": ["view", "launch", "stop"],
    "environments": ["view", "create", "build", "delete"],
    "files": ["view", "upload", "download", "edit", "delete"],
    "projects": ["view", "create", "edit", "delete"],
    "users": ["view", "invite", "edit_role", "deactivate"],
    "infrastructure": ["view", "create", "edit", "configure", "deploy", "change_status", "build"],
    "audit_log": ["view"],
    "notifications": ["view", "configure"],
    "backups": ["view", "create", "restore"],
    "cost_center": ["view", "configure_budgets"],
    "roles": ["view", "create", "edit", "delete"],
    "quotas": ["view", "configure"],
}

COMP_BIO_PERMISSIONS = {
    "experiments": ["view", "create", "edit", "delete", "change_status", "upload"],
    "samples": ["view", "create", "edit", "delete", "change_status"],
    "pipelines": ["view", "create", "edit", "delete", "launch", "cancel", "configure", "change_status"],
    "notebooks": ["view", "create", "edit", "launch", "stop"],
    "work_nodes": ["view", "launch", "stop"],
    "environments": ["view", "create", "build", "delete"],
    "files": ["view", "upload", "download", "edit", "delete"],
    "projects": ["view", "create", "edit", "delete"],
    "users": ["view"],
    "infrastructure": ["view"],
    "audit_log": ["view"],
    "cost_center": ["view"],
}

BENCH_PERMISSIONS = {
    "experiments": ["view", "create", "edit", "upload"],
    "samples": ["view", "create", "edit"],
    "pipelines": ["view"],
    "notebooks": ["view"],
    "environments": ["view"],
    "files": ["view", "upload"],
    "projects": ["view"],
}

VIEWER_PERMISSIONS = {
    "experiments": ["view"],
    "samples": ["view"],
    "environments": ["view"],
    "files": ["view"],
    "projects": ["view"],
}


def _flatten_perms(perm_map: dict[str, list[str]]) -> list[tuple[str, str]]:
    result = []
    for resource, actions in perm_map.items():
        for action in actions:
            result.append((resource, action))
    return result


def upgrade() -> None:
    # 1. Create roles table
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.UniqueConstraint("organization_id", "name", name="uq_roles_org_name"),
    )

    # 2. Create role_permissions table
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("resource", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("role_id", "resource", "action", name="uq_role_resource_action"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])

    # 3. Add role_id column to users (nullable at first for backfill)
    op.add_column("users", sa.Column("role_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_role_id", "users", "roles", ["role_id"], ["id"])

    # 4. Seed built-in roles per organization and backfill users
    conn = op.get_bind()

    org_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM organizations")).fetchall()]

    role_defaults = {
        "admin": ("Full access to all resources", ALL_RESOURCES_ACTIONS),
        "comp_bio": ("Computational biology - full data access, view-only admin", COMP_BIO_PERMISSIONS),
        "bench": ("Bench scientist - create and edit experiments and samples", BENCH_PERMISSIONS),
        "viewer": ("Read-only access to data", VIEWER_PERMISSIONS),
    }

    for org_id in org_ids:
        org_role_ids = {}
        for role_name, (description, perm_map) in role_defaults.items():
            result = conn.execute(
                sa.text(
                    "INSERT INTO roles (name, description, organization_id, is_system) "
                    "VALUES (:name, :description, :org_id, true) RETURNING id"
                ),
                {"name": role_name, "description": description, "org_id": org_id},
            )
            row = result.fetchone()
            assert row is not None
            role_id = row[0]
            org_role_ids[role_name] = role_id

            perms = _flatten_perms(perm_map)
            for resource, action in perms:
                conn.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, resource, action) VALUES (:role_id, :resource, :action)"
                    ),
                    {"role_id": role_id, "resource": resource, "action": action},
                )

        # Backfill users in this organization
        for role_name, role_id in org_role_ids.items():
            conn.execute(
                sa.text("UPDATE users SET role_id = :role_id WHERE organization_id = :org_id AND role = :role_name"),
                {"role_id": role_id, "org_id": org_id, "role_name": role_name},
            )

    # 5. Handle any users with unrecognized roles by assigning viewer
    conn.execute(
        sa.text(
            "UPDATE users SET role_id = ("
            "  SELECT r.id FROM roles r WHERE r.name = 'viewer' "
            "  AND r.organization_id = users.organization_id LIMIT 1"
            ") WHERE role_id IS NULL"
        )
    )

    # 6. Make role_id NOT NULL now that all rows are backfilled
    op.alter_column("users", "role_id", nullable=False)

    # 7. Drop old role string column
    op.drop_column("users", "role")


def downgrade() -> None:
    # Re-add role string column
    op.add_column("users", sa.Column("role", sa.String(50), nullable=True))

    # Backfill role string from roles table
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE users SET role = (  SELECT r.name FROM roles r WHERE r.id = users.role_id)"))
    op.alter_column("users", "role", nullable=False, server_default="viewer")

    # Drop role_id FK and column
    op.drop_constraint("fk_users_role_id", "users", type_="foreignkey")
    op.drop_column("users", "role_id")

    # Drop tables
    op.drop_index("ix_role_permissions_role_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_table("roles")
