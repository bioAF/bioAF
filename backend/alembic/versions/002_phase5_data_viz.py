"""Phase 5: Data Management + Visualization tables.

Revision ID: 002
Revises: 001
Create Date: 2026-03-06

New tables: files, documents, cellxgene_publications, qc_dashboards,
            plot_archive, storage_stats_cache
FK constraints: sample_files.file_id -> files.id,
                notebook_session_files.file_id -> files.id
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # files table
    # ---------------------------------------------------------------
    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("gcs_uri", sa.String(length=1000), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("md5_checksum", sa.String(length=64), nullable=True),
        sa.Column("upload_timestamp", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("uploader_user_id", sa.Integer(), nullable=True),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("tags_json", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["uploader_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_files_org", "files", ["organization_id"])
    op.create_index("idx_files_type", "files", ["file_type"])
    op.create_index("idx_files_uploader", "files", ["uploader_user_id"])

    # ---------------------------------------------------------------
    # documents table
    # ---------------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("linked_experiment_id", sa.Integer(), nullable=True),
        sa.Column("linked_sample_id", sa.Integer(), nullable=True),
        sa.Column("linked_pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["linked_experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["linked_sample_id"], ["samples.id"]),
        sa.ForeignKeyConstraint(["linked_pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_documents_org", "documents", ["organization_id"])
    op.create_index("idx_documents_experiment", "documents", ["linked_experiment_id"])
    op.create_index("idx_documents_file", "documents", ["file_id"])

    # ---------------------------------------------------------------
    # cellxgene_publications table
    # ---------------------------------------------------------------
    op.create_table(
        "cellxgene_publications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("dataset_name", sa.String(length=255), nullable=False),
        sa.Column("stable_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'publishing'"), nullable=False),
        sa.Column("published_by_user_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unpublished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_cellxgene_org", "cellxgene_publications", ["organization_id"])
    op.create_index("idx_cellxgene_experiment", "cellxgene_publications", ["experiment_id"])
    op.create_index("idx_cellxgene_status", "cellxgene_publications", ["status"])

    # ---------------------------------------------------------------
    # qc_dashboards table
    # ---------------------------------------------------------------
    op.create_table(
        "qc_dashboards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("metrics_json", JSONB(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("plots_json", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'generating'"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_qc_dashboards_org", "qc_dashboards", ["organization_id"])
    op.create_index("idx_qc_dashboards_pipeline_run", "qc_dashboards", ["pipeline_run_id"])
    op.create_index("idx_qc_dashboards_experiment", "qc_dashboards", ["experiment_id"])

    # ---------------------------------------------------------------
    # plot_archive table
    # ---------------------------------------------------------------
    op.create_table(
        "plot_archive",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column("notebook_session_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("tags_json", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("thumbnail_gcs_uri", sa.String(length=1000), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["notebook_session_id"], ["notebook_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_plot_archive_org", "plot_archive", ["organization_id"])
    op.create_index("idx_plot_archive_experiment", "plot_archive", ["experiment_id"])
    op.create_index("idx_plot_archive_pipeline", "plot_archive", ["pipeline_run_id"])

    # ---------------------------------------------------------------
    # storage_stats_cache table
    # ---------------------------------------------------------------
    op.create_table(
        "storage_stats_cache",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("bucket_name", sa.String(length=255), nullable=False),
        sa.Column("stats_json", JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------
    # Wire up existing placeholder tables from Phase 2
    # ---------------------------------------------------------------
    op.create_foreign_key("fk_sample_files_file", "sample_files", "files", ["file_id"], ["id"])
    op.create_foreign_key("fk_nsf_file", "notebook_session_files", "files", ["file_id"], ["id"])

    # Grant permissions (conditionally: bioaf_app role may not exist in dev/POC)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bioaf_app') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON files, documents, cellxgene_publications, qc_dashboards, plot_archive, storage_stats_cache TO bioaf_app';
                EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bioaf_app';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_constraint("fk_nsf_file", "notebook_session_files", type_="foreignkey")
    op.drop_constraint("fk_sample_files_file", "sample_files", type_="foreignkey")

    op.drop_index("idx_plot_archive_pipeline")
    op.drop_index("idx_plot_archive_experiment")
    op.drop_index("idx_plot_archive_org")
    op.drop_table("plot_archive")

    op.drop_index("idx_qc_dashboards_experiment")
    op.drop_index("idx_qc_dashboards_pipeline_run")
    op.drop_index("idx_qc_dashboards_org")
    op.drop_table("qc_dashboards")

    op.drop_index("idx_cellxgene_status")
    op.drop_index("idx_cellxgene_experiment")
    op.drop_index("idx_cellxgene_org")
    op.drop_table("cellxgene_publications")

    op.drop_index("idx_documents_file")
    op.drop_index("idx_documents_experiment")
    op.drop_index("idx_documents_org")
    op.drop_table("documents")

    op.drop_index("idx_files_uploader")
    op.drop_index("idx_files_type")
    op.drop_index("idx_files_org")
    op.drop_table("files")

    op.drop_table("storage_stats_cache")
