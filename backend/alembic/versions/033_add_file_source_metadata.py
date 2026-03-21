"""Add source_type and source_pipeline_run_id to files table.

Revision ID: 033
Revises: 032
Create Date: 2026-03-20

Tracks what created each file record (upload, qc_dashboard, plot_archive)
and which pipeline run produced it.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("source_type", sa.String(30), server_default="upload", nullable=False),
    )
    op.add_column(
        "files",
        sa.Column("source_pipeline_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_files_source_pipeline_run",
        "files",
        "pipeline_runs",
        ["source_pipeline_run_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_files_source_pipeline_run", "files", type_="foreignkey")
    op.drop_column("files", "source_pipeline_run_id")
    op.drop_column("files", "source_type")
