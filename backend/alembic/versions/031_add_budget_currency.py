"""Add currency column to budget_config.

Revision ID: 031
Revises: 030
Create Date: 2026-03-16

Allows organizations to configure their budget currency (ISO 4217).
Defaults to USD for existing rows.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "budget_config",
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("budget_config", "currency")
