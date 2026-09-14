"""plan_8_2 sections 1.3 and 1.4: a check record's retry policy and outcome revision survive restarts.

A check whose table could not be fetched was concluded on the spot, and one whose result could not be
stored rolled back the whole queue transaction and stayed pending forever. The retry count, the time of
the next attempt and the terminal reason are now columns, so a bounded retry policy holds across worker
restarts and instances; ``last_attempted_at`` orders the queue fairly; ``outcome_revision`` counts every
change to the record's outcome, so a scorecard can say which outcomes it was built from.

Revision ID: 144
Revises: 143
"""

import sqlalchemy as sa
from alembic import op

revision = "144"
down_revision = "143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("validation_check_records", sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("validation_check_records", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("validation_check_records", sa.Column("terminal_reason", sa.String(40), nullable=True))
    op.add_column("validation_check_records", sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "validation_check_records", sa.Column("outcome_revision", sa.Integer, nullable=False, server_default="0")
    )
    op.create_index("ix_validation_check_records_due", "validation_check_records", ["state", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_validation_check_records_due", table_name="validation_check_records")
    for column in ("outcome_revision", "last_attempted_at", "terminal_reason", "next_attempt_at", "retry_count"):
        op.drop_column("validation_check_records", column)
