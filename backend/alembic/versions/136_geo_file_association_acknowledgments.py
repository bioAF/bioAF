"""GEO file association acknowledgments.

Revision ID: 136
Revises: 135
Create Date: 2026-11-19

GEO export now takes Sample file ownership from sample_files links only. A legacy unlinked file whose
name matches a selected Sample (the old prefix rule) blocks that GEO download until someone links it or
acknowledges, through the running API, that it stays Experiment-level. This table holds those
acknowledgments, one row per file/Sample pair and policy revision.

Creates an empty table only: no acknowledgment, link or scientific data is written or inferred here, and
legacy findings never fail the migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "136"
down_revision = "135"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geo_file_association_acknowledgments",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("experiment_id", sa.Integer, sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sample_id", sa.Integer, sa.ForeignKey("samples.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_revision", sa.Integer, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("acknowledged_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "experiment_id", "file_id", "sample_id", "policy_revision", name="uq_geo_file_association_ack_pair"
        ),
    )
    op.create_index(
        "ix_geo_file_association_ack_org_experiment",
        "geo_file_association_acknowledgments",
        ["organization_id", "experiment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_geo_file_association_ack_org_experiment", table_name="geo_file_association_acknowledgments")
    op.drop_table("geo_file_association_acknowledgments")
