"""Add column_aliases JSONB to experiments.

Revision ID: 074
Revises: 073
Create Date: 2026-05-09

Persists GSheet header to sample-field mappings made during experiment
creation so that subsequent sample imports from the same sheet do not
re-classify those columns as unknown / new custom fields.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("column_aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiments", "column_aliases")
