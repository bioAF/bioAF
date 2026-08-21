"""Saved samplesheet mappings, scoped to an experiment and promotable upward.

Revision ID: 115
Revises: 114
Create Date: 2026-08-15

Additive only. Nothing reads this table until the entry grid ships, and a launch
with no mapping behaves exactly as it does today, so there is nothing to backfill.

Scoped to an EXPERIMENT by default because the binding depends on the experiment:
the right column for one is the wrong one for the next. Promotable to the project
and then to the organization, deliberately at each rung. The organization rung is
not a convenience: a core facility runs the same assay across many unrelated
projects and would otherwise reconfigure indefinitely.

One current mapping per pipeline per scope, expressed as three PARTIAL unique
indexes rather than one composite constraint. PostgreSQL treats NULLs as distinct
in a unique constraint, so a single constraint over the nullable scope columns
would let organization-scoped duplicates through, which is the one case that
matters because it is the rung that reaches people who did not choose it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "115"
down_revision = "114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "samplesheet_mappings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("pipeline_key", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("bindings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("values_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_samplesheet_mappings_lookup",
        "samplesheet_mappings",
        ["organization_id", "pipeline_key"],
    )
    op.create_index(
        "uq_samplesheet_mapping_experiment",
        "samplesheet_mappings",
        ["experiment_id", "pipeline_key"],
        unique=True,
        postgresql_where=sa.text("scope = 'experiment'"),
    )
    op.create_index(
        "uq_samplesheet_mapping_project",
        "samplesheet_mappings",
        ["project_id", "pipeline_key"],
        unique=True,
        postgresql_where=sa.text("scope = 'project'"),
    )
    op.create_index(
        "uq_samplesheet_mapping_organization",
        "samplesheet_mappings",
        ["organization_id", "pipeline_key"],
        unique=True,
        postgresql_where=sa.text("scope = 'organization'"),
    )


def downgrade() -> None:
    op.drop_index("uq_samplesheet_mapping_organization", table_name="samplesheet_mappings")
    op.drop_index("uq_samplesheet_mapping_project", table_name="samplesheet_mappings")
    op.drop_index("uq_samplesheet_mapping_experiment", table_name="samplesheet_mappings")
    op.drop_index("ix_samplesheet_mappings_lookup", table_name="samplesheet_mappings")
    op.drop_table("samplesheet_mappings")
