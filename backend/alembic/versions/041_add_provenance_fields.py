"""Add provenance-related fields to experiments, samples, files, and pipeline_runs.

Supports the provenance reporting system (ADR-037). All new columns are nullable
except retry_count which has a server default of 0.

Revision ID: 041
Revises: 040
Create Date: 2026-03-24
"""

import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Experiment
    op.add_column("experiments", sa.Column("design_type", sa.String(100), nullable=True))
    op.add_column("experiments", sa.Column("protocol_version", sa.String(50), nullable=True))
    op.add_column("experiments", sa.Column("variables_json", sa.dialects.postgresql.JSONB, nullable=True))

    # Sample
    op.add_column("samples", sa.Column("parent_sample_id", sa.Integer, nullable=True))
    op.add_column("samples", sa.Column("collection_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("samples", sa.Column("collection_method", sa.String(200), nullable=True))
    op.create_foreign_key("fk_samples_parent_sample_id", "samples", "samples", ["parent_sample_id"], ["id"])

    # File
    op.add_column("files", sa.Column("sha256_checksum", sa.String(64), nullable=True))
    op.add_column("files", sa.Column("artifact_type", sa.String(50), nullable=True))

    # Pipeline Run
    op.add_column("pipeline_runs", sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("pipeline_runs", sa.Column("reviewed_by_user_id", sa.Integer, nullable=True))
    op.add_column("pipeline_runs", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_pipeline_runs_reviewed_by_user_id", "pipeline_runs", "users", ["reviewed_by_user_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_pipeline_runs_reviewed_by_user_id", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "reviewed_at")
    op.drop_column("pipeline_runs", "reviewed_by_user_id")
    op.drop_column("pipeline_runs", "retry_count")
    op.drop_column("files", "artifact_type")
    op.drop_column("files", "sha256_checksum")
    op.drop_constraint("fk_samples_parent_sample_id", "samples", type_="foreignkey")
    op.drop_column("samples", "collection_method")
    op.drop_column("samples", "collection_timestamp")
    op.drop_column("samples", "parent_sample_id")
    op.drop_column("experiments", "variables_json")
    op.drop_column("experiments", "protocol_version")
    op.drop_column("experiments", "design_type")
