"""Add code column to projects and experiments.

Revision ID: 043
Revises: 042
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("code", sa.String(20), nullable=True))
    op.add_column("experiments", sa.Column("code", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "code")
    op.drop_column("experiments", "code")
