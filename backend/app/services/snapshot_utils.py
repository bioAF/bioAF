"""Utility for serializing SQLAlchemy models to snapshot dicts."""

from datetime import date, datetime
from decimal import Decimal


def serialize_entity(obj) -> dict:
    """Serialize a SQLAlchemy model instance to a JSON-safe dict.

    Uses obj.__dict__ to avoid triggering lazy loads on expired attributes.
    Handles datetime, date, and Decimal types. Skips relationship attributes
    and SQLAlchemy internal state.
    """
    col_names = {col.name for col in obj.__table__.columns}
    result = {}
    for key, val in obj.__dict__.items():
        if key.startswith("_") or key not in col_names:
            continue
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, date):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        result[key] = val
    return result
