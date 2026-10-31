"""Seed the OpenAlex literature source for existing organizations.

Revision ID: 128
Revises: 127
Create Date: 2026-10-29

New organizations get it from seed_literature_sources; this backfills the
ones that already exist, enabled like the other built-in sources (083).
"""

import sqlalchemy as sa
from alembic import op

revision = "128"
down_revision = "127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO literature_sources_config (organization_id, source, enabled)
            SELECT o.id, 'openalex', true
            FROM organizations o
            WHERE NOT EXISTS (
                SELECT 1 FROM literature_sources_config c
                WHERE c.organization_id = o.id AND c.source = 'openalex'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM literature_sources_config WHERE source = 'openalex'")
