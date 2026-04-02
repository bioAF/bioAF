"""Add deploy_phase and completed_resources to terraform_runs.

Supports the poll-based deployment progress UI. The frontend polls
for progress instead of maintaining an SSE connection, so phase
and per-resource completion must be persisted server-side.

Revision ID: 054
Revises: 053
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "terraform_runs",
        sa.Column("deploy_phase", sa.String(50), nullable=True),
    )
    op.add_column(
        "terraform_runs",
        sa.Column("completed_resources", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("terraform_runs", "completed_resources")
    op.drop_column("terraform_runs", "deploy_phase")
