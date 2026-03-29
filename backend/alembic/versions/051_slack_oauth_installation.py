"""Add Slack OAuth installation and channel mapping tables.

Revision ID: 051
Revises: 050
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "051"
down_revision = "050"


def upgrade() -> None:
    op.create_table(
        "slack_installations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False, unique=True),
        sa.Column("team_id", sa.String(50), nullable=False),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("bot_token", sa.String(500), nullable=False),
        sa.Column("bot_user_id", sa.String(50), nullable=False),
        sa.Column("authed_user_id", sa.String(50), nullable=True),
        sa.Column("installed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "slack_channel_mappings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("channel_id", sa.String(50), nullable=False),
        sa.Column("channel_name", sa.String(255), nullable=False),
        sa.Column("event_types_json", JSONB, server_default="[]", nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_slack_channel_mappings_org", "slack_channel_mappings", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_slack_channel_mappings_org")
    op.drop_table("slack_channel_mappings")
    op.drop_table("slack_installations")
