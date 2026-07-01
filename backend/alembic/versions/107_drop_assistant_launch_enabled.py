"""Drop the per-org assistant_launch_enabled toggle.

ai_pipeline_run: the assistant no longer gates real launches behind a per-org toggle. The
plan-then-confirm gate is the safety boundary (the user explicitly confirms every consequential
action), and the confirm UI now warns when an action will spend compute. So confirming a launch
always launches for real, and the toggle column is removed.

Revision ID: 107
Revises: 106
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op

revision = "107"
down_revision = "106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("organizations", "assistant_launch_enabled")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("assistant_launch_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
