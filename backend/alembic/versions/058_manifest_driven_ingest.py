"""Manifest-driven ingest with batch separation.

Revision ID: 058
Revises: 057

Additive-only migration. Creates new tables for sample batches,
sequencing batches, manifest entries, and entity snapshots. Adds
FK columns to samples and files. Does NOT modify or drop any
existing tables or columns.
"""

from alembic import op
import sqlalchemy as sa

revision = "058"
down_revision = "057"


def upgrade() -> None:
    # --- New table: sample_batches ---
    # Mirrors the original batches table structure with all columns preserved.
    # The legacy 'batches' table and 'batch_id' FK on samples remain untouched.
    op.create_table(
        "sample_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("prep_date", sa.Date(), nullable=True),
        sa.Column("operator_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sequencer_run_id", sa.String(255), nullable=True),
        sa.Column("instrument_model", sa.String(200), nullable=True),
        sa.Column("instrument_platform", sa.String(100), nullable=True),
        sa.Column("quality_score_encoding", sa.String(50), server_default="Phred+33", nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- New table: sequencing_batches ---
    op.create_table(
        "sequencing_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("code", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("instrument_model", sa.String(200), nullable=True),
        sa.Column("instrument_platform", sa.String(100), nullable=True),
        sa.Column("quality_score_encoding", sa.String(50), server_default="Phred+33", nullable=True),
        sa.Column("sequencer_run_id", sa.String(255), nullable=True),
        sa.Column("manifest_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_file_count", sa.Integer(), nullable=True),
        sa.Column("ingested_file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- New table: manifest_entries ---
    op.create_table(
        "manifest_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sequencing_batch_id", sa.Integer(), sa.ForeignKey("sequencing_batches.id"), nullable=False),
        sa.Column("expected_filename", sa.String(500), nullable=False),
        sa.Column("expected_md5", sa.String(64), nullable=False),
        sa.Column("resolved_sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=True),
        sa.Column("resolved_experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=True),
        sa.Column("resolved_project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id"), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- New table: entity_snapshots ---
    op.create_table(
        "entity_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("audit_log_id", sa.BigInteger(), sa.ForeignKey("audit_log.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_entity_snapshots_type_id", "entity_snapshots", ["entity_type", "entity_id"])

    # --- Add FK columns to samples (leave existing batch_id untouched) ---
    op.add_column(
        "samples",
        sa.Column("sample_batch_id", sa.Integer(), sa.ForeignKey("sample_batches.id"), nullable=True),
    )
    op.add_column(
        "samples",
        sa.Column("sequencing_batch_id", sa.Integer(), sa.ForeignKey("sequencing_batches.id"), nullable=True),
    )

    # --- Add FK column to files ---
    op.add_column(
        "files",
        sa.Column("sequencing_batch_id", sa.Integer(), sa.ForeignKey("sequencing_batches.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("files", "sequencing_batch_id")
    op.drop_column("samples", "sequencing_batch_id")
    op.drop_column("samples", "sample_batch_id")
    op.drop_index("ix_entity_snapshots_type_id", "entity_snapshots")
    op.drop_table("entity_snapshots")
    op.drop_table("manifest_entries")
    op.drop_table("sequencing_batches")
    op.drop_table("sample_batches")
