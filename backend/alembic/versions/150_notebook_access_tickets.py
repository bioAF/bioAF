"""Single-use notebook access tickets (plan 03.5A).

Revision ID: 150
Revises: 149
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "150"
down_revision = "149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notebook_access_tickets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("compute_sessions.id"), nullable=False),
        sa.Column("pod_name", sa.String(255), nullable=False),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("pod_uid", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notebook_access_tickets_session_id", "notebook_access_tickets", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_notebook_access_tickets_session_id", table_name="notebook_access_tickets")
    op.drop_table("notebook_access_tickets")
