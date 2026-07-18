"""ReproductionPlan + ComparisonTarget tables (lit_validation B2/B3, spec-02).

Revision ID: 109
Revises: 108
Create Date: 2026-07-03

Additive only. Creates reproduction_plans (the reviewable "read the paper" output for a
ValidationStudy) and comparison_targets (the paper's quantitative claims). Mirrors
app/models/reproduction_plan.py and app/models/comparison_target.py (the source of truth the test
suite builds via create_all).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reproduction_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("validation_study_id", sa.Integer(), nullable=False),
        sa.Column("accessions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sample_sheet_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pipeline_key", sa.String(100), nullable=True),
        sa.Column("pipeline_version", sa.String(50), nullable=True),
        sa.Column("parameters_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_genome", sa.String(100), nullable=True),
        sa.Column("reference_build", sa.String(100), nullable=True),
        sa.Column("mapping_confidence", sa.String(20), nullable=True),
        sa.Column("mapping_notes", sa.Text(), nullable=True),
        sa.Column("blockers_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extractor_model", sa.String(100), nullable=True),
        sa.Column("extractor_provider", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["validation_study_id"], ["validation_studies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reproduction_plans_validation_study_id",
        "reproduction_plans",
        ["validation_study_id"],
    )

    op.create_table(
        "comparison_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reproduction_plan_id", sa.Integer(), nullable=False),
        sa.Column("metric_key", sa.String(100), nullable=False),
        sa.Column("claimed_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("tolerance", sa.Float(), nullable=True),
        sa.Column("source_locator", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["reproduction_plan_id"], ["reproduction_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_comparison_targets_reproduction_plan_id",
        "comparison_targets",
        ["reproduction_plan_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_comparison_targets_reproduction_plan_id", table_name="comparison_targets")
    op.drop_table("comparison_targets")
    op.drop_index("ix_reproduction_plans_validation_study_id", table_name="reproduction_plans")
    op.drop_table("reproduction_plans")
