"""Add Slack OAuth credentials to organizations table.

Revision ID: 052
Revises: 051
"""

from alembic import op
import sqlalchemy as sa

revision = "052"
down_revision = "051"


def upgrade() -> None:
    op.add_column("organizations", sa.Column("slack_client_id", sa.String(255), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("slack_client_secret", sa.String(500), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("slack_signing_secret", sa.String(255), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("organizations", "slack_signing_secret")
    op.drop_column("organizations", "slack_client_secret")
    op.drop_column("organizations", "slack_client_id")
