"""Add the optional first-class assay column to samples.

Hybrid assay design (ai_pipeline_run): recommend_pipeline prefers this controlled-vocabulary
field when it is set and otherwise falls back to inferring the assay from molecule_type /
chemistry_version / library_prep_method. The column is optional and existing rows are left
null (the read-time heuristic still covers them), so this migration is purely additive.

Revision ID: 105
Revises: 104
Create Date: 2026-06-24
"""

import sqlalchemy as sa
from alembic import op

revision = "105"
down_revision = "104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("assay", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("samples", "assay")
