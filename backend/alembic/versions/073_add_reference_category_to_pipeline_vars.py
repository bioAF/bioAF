"""Add `reference_category` column to custom_pipeline_variables.

Revision ID: 073
Revises: 072
Create Date: 2026-05-04

Spec §5 — when variable_type='reference', this column scopes the
launch-time dropdown to a single reference category. Nullable because
existing string/number/boolean variables don't use it.
"""

import sqlalchemy as sa
from alembic import op

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_pipeline_variables",
        sa.Column("reference_category", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_pipeline_variables", "reference_category")
