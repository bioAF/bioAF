"""Plot Archive scan health.

Revision ID: 140
Revises: 139
Create Date: 2026-11-21

Adds ``plot_scan_state``, the watcher's durable record of the results store's health, its last
successful scan, and when it next lists the store. Empty: the running application fills it.
"""

import sqlalchemy as sa
from alembic import op

revision = "140"
down_revision = "139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plot_scan_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("results_bucket", sa.Text, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("plot_scan_state")
