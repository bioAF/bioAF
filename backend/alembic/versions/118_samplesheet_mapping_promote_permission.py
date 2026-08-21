"""Seed samplesheet_mappings:promote_organization for existing organizations.

Revision ID: 118
Revises: 117
Create Date: 2026-08-17

Additive only. A samplesheet design is authored by anyone who can launch in the
experiment and promoted to a project by anyone with project access, both of
which reuse permissions that already exist. Promoting one to the organization
is the rung where a decision reaches people who did not choose it, so it gets
its own grant, held by admin alone.

New organizations get it from app/services/bootstrap_roles.py; this backfills
the system admin role of every organization that already exists. Idempotent:
the INSERT skips roles that already hold it.
"""

from alembic import op

revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role_id, resource, action)
        SELECT r.id, 'samplesheet_mappings', 'promote_organization'
        FROM roles r
        WHERE r.name = 'admin' AND r.is_system = true
        AND NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.role_id = r.id
              AND rp.resource = 'samplesheet_mappings'
              AND rp.action = 'promote_organization'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE resource = 'samplesheet_mappings'")
