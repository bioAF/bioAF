"""Add access_url to cellxgene_publications.

Revision ID: 056
Revises: 055
"""

from alembic import op
import sqlalchemy as sa

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cellxgene_publications",
        sa.Column("access_url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cellxgene_publications", "access_url")
