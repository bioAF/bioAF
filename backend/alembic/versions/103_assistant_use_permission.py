"""Seed the assistant:use RBAC permission for existing organizations.

Revision ID: 103
Revises: 102
Create Date: 2026-06-24

Additive only (ai_pipeline_run Phase 1). The conversational assistant is gated by a new
permission, assistant:use, granted to admin, comp_bio, and bench at bootstrap (see
app/services/bootstrap_roles.py). New organizations get it via that bootstrap; this
migration backfills it onto the system roles of every organization that already exists.
Idempotent: each INSERT skips roles that already hold the grant. viewer is intentionally
excluded (a read-only role cannot drive an action-taking agent).
"""

from alembic import op

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None

_ROLES = ("admin", "comp_bio", "bench")


def upgrade() -> None:
    for role_name in _ROLES:
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, resource, action)
            SELECT r.id, 'assistant', 'use'
            FROM roles r
            WHERE r.name = '{role_name}' AND r.is_system = true
            AND NOT EXISTS (
                SELECT 1 FROM role_permissions rp
                WHERE rp.role_id = r.id
                  AND rp.resource = 'assistant'
                  AND rp.action = 'use'
            )
            """
        )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE resource = 'assistant' AND action = 'use'")
