"""Add is_required column to experiment_custom_fields.

Revision ID: 059
Revises: 058
"""

from alembic import op
import sqlalchemy as sa

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiment_custom_fields",
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("experiment_custom_fields", "is_required")
