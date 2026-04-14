"""Add failure_reason to pipeline_runs.

Revision ID: 066
Revises: 065
Create Date: 2026-04-14

Adds a nullable failure_reason column so the UI can render specific
treatments for OOM, preemption exhaustion, and generic task errors
without parsing error message text.
"""

import sqlalchemy as sa
from alembic import op

revision = "066"
down_revision = "065"


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("failure_reason", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_runs", "failure_reason")
