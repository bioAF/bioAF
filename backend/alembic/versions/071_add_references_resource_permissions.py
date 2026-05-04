"""Backfill `references` resource permissions for existing system roles.

Revision ID: 071
Revises: 070
Create Date: 2026-05-04

The Reference Data Ingest spec (ADR-047) introduces a dedicated
`references` resource with `view` and `upload` actions, replacing the
prior reuse of `pipelines:*` for reference data CRUD. Bootstrap_roles
seeds these for new orgs; this migration backfills existing system
roles (admin, comp_bio, bench, viewer).
"""

from alembic import op

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # admin and comp_bio: view + upload
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, perm.resource, perm.action
        FROM roles r
        CROSS JOIN (VALUES ('references', 'view'), ('references', 'upload')) AS perm(resource, action)
        WHERE r.name IN ('admin', 'comp_bio') AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )
    # bench and viewer: view only
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'references', 'view'
        FROM roles r
        WHERE r.name IN ('bench', 'viewer') AND r.is_system = true
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE resource = 'references'
        """
    )
