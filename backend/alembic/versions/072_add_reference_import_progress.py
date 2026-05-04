"""Add reference_import_progress table.

Revision ID: 072
Revises: 071
Create Date: 2026-05-04

Spec §3 — tracks GKE-job-driven reference imports. One row per
ReferenceDataset (1:1 via reference_id PK with cascade delete).
"""

import sqlalchemy as sa
from alembic import op

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_import_progress",
        sa.Column(
            "reference_id",
            sa.Integer(),
            sa.ForeignKey("reference_datasets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("import_job_id", sa.String(length=100), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=True),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("reference_import_progress")
