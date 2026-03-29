"""Add SSH key columns to session_credentials.

Revision ID: 050
Revises: 049
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_credentials", sa.Column("ssh_public_key", sa.Text(), nullable=True))
    op.add_column("session_credentials", sa.Column("ssh_private_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("session_credentials", "ssh_private_key")
    op.drop_column("session_credentials", "ssh_public_key")
