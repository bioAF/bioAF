"""Add sample_custom_fields table.

Revision ID: 060
Revises: 059

Stores per-sample values for custom fields defined at the experiment level.
"""

from alembic import op
import sqlalchemy as sa

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sample_custom_fields",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=True),
    )
    op.create_index("ix_sample_custom_fields_sample_id", "sample_custom_fields", ["sample_id"])


def downgrade() -> None:
    op.drop_index("ix_sample_custom_fields_sample_id", "sample_custom_fields")
    op.drop_table("sample_custom_fields")
