"""Add the per-org assistant_launch_enabled toggle.

ai_pipeline_run: when enabled (admin-only org setting), the conversational assistant's confirm
step actually launches a PipelineRun via the normal launch path instead of only building the
request. Default OFF, so spending compute through the agent is an explicit per-org opt-in. The
column is additive and existing rows default to false.

Revision ID: 106
Revises: 105
Create Date: 2026-06-27
"""

import sqlalchemy as sa
from alembic import op

revision = "106"
down_revision = "105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("assistant_launch_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "assistant_launch_enabled")
