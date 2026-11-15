"""Reserved installation metrics reports (``install_metric_events``, ADR-075).

Reserving never touches the network. A source key reserves at most one report, ever: a cancelled or
expired report keeps its key, so a later attempt for the same source reserves nothing.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.install_metrics import payload as allowlist

BASELINE_SOURCE = "baseline"
MAX_PENDING_UPGRADES = 100

PENDING = "pending"
SENT = "sent"
CANCELLED = "cancelled"
EXPIRED = "expired"


def upgrade_source(installation_id: str, operation_id: str) -> str:
    return f"upgrade:{installation_id}:{operation_id}"


async def reserve(
    session: AsyncSession,
    *,
    source_key: str,
    name: str,
    installation_id: str,
    moment: datetime,
    bioaf_version: object,
    from_version: object = None,
) -> bool:
    """Reserve the report for ``source_key``; False if that source already reserved one."""
    day = allowlist.report_day(moment)
    event_uuid = str(uuid.uuid4())
    event = allowlist.build_event(
        name=name,
        event_uuid=event_uuid,
        installation_id=installation_id,
        day=day,
        bioaf_version=bioaf_version,
        from_version=from_version,
    )
    row = (
        await session.execute(
            text(
                "INSERT INTO install_metric_events "
                "(source_key, event_uuid, event_name, payload, report_date, status, attempts, next_attempt_at, "
                "created_at) "
                "VALUES (:source_key, :event_uuid, :name, CAST(:payload AS JSONB), :day, 'pending', 0, :moment, "
                ":moment) "
                "ON CONFLICT (source_key) DO NOTHING RETURNING id"
            ),
            {
                "source_key": source_key,
                "event_uuid": event_uuid,
                "name": name,
                "payload": _json(event),
                "day": day,
                "moment": moment,
            },
        )
    ).first()
    return row is not None


async def pending_upgrades(session: AsyncSession) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM install_metric_events WHERE status = 'pending' AND event_name = :name"),
            {"name": allowlist.UPGRADE_COMPLETED},
        )
    ).scalar_one()


async def cancel_pending(session: AsyncSession, now: datetime) -> int:
    """Cancel every pending report. Cancelled reports are never sent or replayed."""
    result = await session.execute(
        text(
            "UPDATE install_metric_events SET status = 'cancelled', payload = NULL, settled_at = :now "
            "WHERE status = 'pending'"
        ),
        {"now": now},
    )
    return result.rowcount or 0


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
