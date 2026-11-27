"""Record each Work Node report attempt and its failure, separately from the last successful report.

Revision ID: 149
Revises: 148
Create Date: 2026-11-25

Plan 03.5D. ``heartbeat_at`` stays the time of the last successful report; ``report_attempted_at``
is the latest attempt (also the per-node claim and fairness cursor) and ``report_failure`` the
bounded reason the latest attempt failed. Nullable, nothing backfilled.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "149"
down_revision = "148"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_sessions", sa.Column("report_attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compute_sessions", sa.Column("report_failure", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("compute_sessions", "report_failure")
    op.drop_column("compute_sessions", "report_attempted_at")
