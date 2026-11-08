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

revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None

# Frozen here (plan 9): this was imported from app.services.notification_backfill, so a later edit to
# that module would have changed what an install skipping releases ran. Byte-for-byte the SQL
# published with this migration in v2026.5.16 (held by tests/test_frozen_migrations.py).
#
# ``jsonb_exists(col, key)`` is used instead of the ``?`` operator so the SQL has
# no ``?`` that a driver could mistake for a bind placeholder.
BACKFILL_NOTIFICATION_LINKS_SQL = """
UPDATE notifications n
SET metadata_json = jsonb_set(
        jsonb_set(
            coalesce(n.metadata_json, '{}'::jsonb),
            '{entity_type}', to_jsonb(a.entity_type)
        ),
        '{entity_id}', to_jsonb(a.entity_id)
    )
FROM activity_feed a
WHERE a.organization_id = n.organization_id
  AND a.event_type = n.event_type
  AND a.created_at = n.created_at
  AND a.entity_type IS NOT NULL
  AND a.entity_id IS NOT NULL
  AND NOT jsonb_exists(n.metadata_json, 'entity_type')
"""


def upgrade() -> None:
    op.execute(BACKFILL_NOTIFICATION_LINKS_SQL)


def downgrade() -> None:
    # One-time data backfill; there is nothing to reverse.
    pass
