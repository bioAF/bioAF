"""plan_8_1 section 3.2: durable, revisioned check records, one per plan, claim and check kind.

``validation_check_records`` replaces the single-slot evidence artifacts for studies read after it: each
check keeps its own attempts, outcome and superseded revisions, so a second check of the same kind never
overwrites the first. Studies made before it keep rendering from their single-slot artifacts.

Revision ID: 143
Revises: 142
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "143"
down_revision = "142"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_check_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "validation_study_id",
            sa.Integer,
            sa.ForeignKey("validation_studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reproduction_plan_id",
            sa.Integer,
            sa.ForeignKey("reproduction_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comparison_target_id",
            sa.Integer,
            sa.ForeignKey("comparison_targets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("check_id", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("dependencies_json", JSONB, nullable=True),
        sa.Column("dependencies_fingerprint", sa.String(64), nullable=True),
        sa.Column("analysis_key", sa.String(64), nullable=True),
        sa.Column("approval_id", sa.String(64), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts_json", JSONB, nullable=True),
        sa.Column("outcome_json", JSONB, nullable=True),
        sa.Column("history_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("check_id", name="uq_validation_check_records_check_id"),
    )
    op.create_index(
        "ix_validation_check_records_validation_study_id", "validation_check_records", ["validation_study_id"]
    )
    op.create_index(
        "ix_validation_check_records_reproduction_plan_id", "validation_check_records", ["reproduction_plan_id"]
    )
    op.create_index("ix_validation_check_records_analysis_key", "validation_check_records", ["analysis_key"])


def downgrade() -> None:
    op.drop_index("ix_validation_check_records_analysis_key", table_name="validation_check_records")
    op.drop_index("ix_validation_check_records_reproduction_plan_id", table_name="validation_check_records")
    op.drop_index("ix_validation_check_records_validation_study_id", table_name="validation_check_records")
    op.drop_table("validation_check_records")
