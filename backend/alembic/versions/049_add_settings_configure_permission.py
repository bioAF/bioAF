"""Add settings:configure permission to admin roles.

Revision ID: 049
Revises: 048
Create Date: 2026-03-27
"""

from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add settings:view and settings:configure to all admin roles
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, perm.resource, perm.action
        FROM roles r
        CROSS JOIN (VALUES ('settings', 'view'), ('settings', 'configure')) AS perm(resource, action)
        WHERE r.name = 'admin' AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE resource = 'settings'
        """
    )
