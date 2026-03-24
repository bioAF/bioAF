"""Add SMTP credential columns to organizations table.

Persists SMTP settings in the database instead of only in-memory.

Revision ID: 040
Revises: 039
Create Date: 2026-03-24
"""

import sqlalchemy as sa
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("smtp_host", sa.String(255), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("smtp_port", sa.Integer(), server_default="587", nullable=False))
    op.add_column("organizations", sa.Column("smtp_username", sa.String(255), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("smtp_password", sa.String(500), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("smtp_from_address", sa.String(255), server_default="", nullable=False))
    op.add_column(
        "organizations", sa.Column("smtp_encryption", sa.String(20), server_default="starttls", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("organizations", "smtp_encryption")
    op.drop_column("organizations", "smtp_from_address")
    op.drop_column("organizations", "smtp_password")
    op.drop_column("organizations", "smtp_username")
    op.drop_column("organizations", "smtp_port")
    op.drop_column("organizations", "smtp_host")
