"""Backfill entity references onto historical notifications so they deep-link.

Revision ID: 087
Revises: 086
Create Date: 2026-05-21

Notifications created before they became clickable have an empty metadata_json,
so the UI cannot deep-link them. This copies entity_type/entity_id from the
activity feed, matched on (organization_id, event_type, created_at): each event
writes its feed entry and its notifications in one transaction, so they share an
exact created_at. Only notifications that lack an entity reference are filled.
"""

from alembic import op

from app.services.notification_backfill import BACKFILL_NOTIFICATION_LINKS_SQL

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(BACKFILL_NOTIFICATION_LINKS_SQL)


def downgrade() -> None:
    # One-time data backfill; there is nothing to reverse.
    pass
