"""Bucket filter for the Notebook / Work Node list endpoints.

UI shows three filter buttons over a session list:

    Active  Recent  All

- Active: every session that is still doing something (NOT in a terminal
  state). Mirrors what an operator wants to monitor right now: pending,
  starting, running, idle, stopping.
- Recent: every session created in the last 24h regardless of status, so
  the user can see what *just* failed without scrolling.
- All:    no filter.

The set of terminal statuses is shared by notebooks and work nodes: 'stopped'
and 'failed'. Anything else counts as active.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException


VALID_BUCKETS = frozenset({"active", "recent", "all"})
TERMINAL_STATUSES = frozenset({"stopped", "failed"})
RECENT_WINDOW = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bucket_filter(model, bucket: str | None):
    """Return a SQLAlchemy where-clause for the given bucket, or None.

    None means no extra filter (active=None and bucket='all' both fall here).
    Raises HTTPException(400) for unknown values so the API rejects typos
    instead of silently treating them as 'all'.
    """
    if bucket is None or bucket == "all":
        return None
    if bucket not in VALID_BUCKETS:
        raise HTTPException(status_code=400, detail=f"Unknown bucket '{bucket}'. Valid: active, recent, all.")
    if bucket == "active":
        return model.status.notin_(TERMINAL_STATUSES)
    # bucket == "recent"
    return model.created_at >= _utc_now() - RECENT_WINDOW
