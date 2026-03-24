"""Add work_nodes.configure permission to existing admin roles.

Backfills the permission for orgs created before this migration.

Revision ID: 039
Revises: 038
Create Date: 2026-03-24
"""

from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'work_nodes', 'configure'
        FROM roles r
        WHERE r.name = 'admin' AND r.is_system = true
          AND NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.role_id = r.id AND rp.resource = 'work_nodes' AND rp.action = 'configure'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE resource = 'work_nodes' AND action = 'configure'
        """
    )
