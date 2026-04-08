"""Add sequencing_batch_position to samples for S-number mapping.

Revision ID: 062
Revises: 061

Tracks each sample's ordinal position within its sequencing batch,
enabling auto-ingest to map Illumina S-numbers (S1, S2, etc.) back
to the correct sample.
"""

from alembic import op
import sqlalchemy as sa

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("sequencing_batch_position", sa.Integer(), nullable=True))
    op.create_index(
        "uq_sample_seq_batch_position",
        "samples",
        ["sequencing_batch_id", "sequencing_batch_position"],
        unique=True,
        postgresql_where=sa.text("sequencing_batch_id IS NOT NULL AND sequencing_batch_position IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_sample_seq_batch_position", table_name="samples")
    op.drop_column("samples", "sequencing_batch_position")
