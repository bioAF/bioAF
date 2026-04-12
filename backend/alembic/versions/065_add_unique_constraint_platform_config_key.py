"""Add unique constraint to platform_config.key.

Revision ID: 065
Revises: 064
Create Date: 2026-04-11

The model declares unique=True on PlatformConfig.key but the original
migration 001 omitted the constraint. Without it, duplicate keys can
accumulate and break scalar_one_or_none() queries (see issue #151).

This migration deduplicates any existing rows first (keeping the most
recently updated value per key), then adds the constraint.
"""

from alembic import op

revision = "065"
down_revision = "064"


def upgrade() -> None:
    # Deduplicate: keep the row with the highest id for each key
    op.execute(
        """
        DELETE FROM platform_config
        WHERE id NOT IN (
            SELECT MAX(id) FROM platform_config GROUP BY key
        )
        """
    )

    op.create_unique_constraint("uq_platform_config_key", "platform_config", ["key"])


def downgrade() -> None:
    op.drop_constraint("uq_platform_config_key", "platform_config", type_="unique")
