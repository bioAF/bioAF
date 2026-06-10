"""Backfill entity references onto historical in-app notifications.

Notifications created before they became clickable have an empty ``metadata_json``,
so the UI cannot deep-link them. The activity feed recorded the
``entity_type``/``entity_id`` for the same events, and because each event writes
its feed entry and its notifications inside one transaction they share an exact
``created_at`` (Postgres ``now()`` is the transaction time). We copy the entity
reference across on ``(organization_id, event_type, created_at)``, only filling
notifications that don't already carry one.
"""

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
