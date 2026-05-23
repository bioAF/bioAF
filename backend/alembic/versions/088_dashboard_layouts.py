"""Add per-user dashboard layout storage.

Revision ID: 088
Revises: 087
Create Date: 2026-05-23

Additive only: dashboard_layouts holds each user's customizable dashboard widget
selection as a JSONB array of {key, settings}. One row per user; absence of a row
means the user has never configured their dashboard (frontend seeds role defaults).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_layouts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "widgets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_dashboard_layouts_user"),
    )


def downgrade() -> None:
    op.drop_table("dashboard_layouts")
