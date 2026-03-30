"""Auto-ingest settings API endpoints (Phase 21).

- POST /api/v1/settings/auto-ingest  - enable/disable auto-ingest
- GET  /api/v1/settings/auto-ingest  - get auto-ingest status
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.ingest_event import IngestEvent
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.auto_ingest_api")

router = APIRouter(tags=["auto_ingest"])


class AutoIngestConfig(BaseModel):
    enabled: bool
    cleanup_policy: str = "delete_after_copy"
    default_delay_minutes: int | None = None


class AutoIngestStatus(BaseModel):
    enabled: bool
    cleanup_policy: str
    default_delay_minutes: int
    listener_running: bool
    pubsub_topic: str | None
    pubsub_subscription: str | None
    messages_processed_24h: int
    messages_failed_24h: int


@router.post("/api/v1/settings/auto-ingest")
async def configure_auto_ingest(
    body: AutoIngestConfig,
    current_user: dict = require_permission("pipelines", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Enable or disable auto-ingest with optional cleanup policy."""
    user_id = int(current_user["sub"])

    # Check storage is deployed
    if body.enabled:
        row = await session.execute(text("SELECT value FROM platform_config WHERE key = 'storage_deployed'"))
        storage = row.scalar()
        if not storage or storage != "true":
            raise HTTPException(
                status_code=400,
                detail="Storage infrastructure must be deployed before enabling auto-ingest.",
            )

    # Validate cleanup policy
    valid_policies = {"delete_after_copy", "retain_7d", "retain_30d"}
    if body.cleanup_policy not in valid_policies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid cleanup policy. Must be one of: {', '.join(valid_policies)}",
        )

    # Update platform_config
    enabled_value = "true" if body.enabled else "false"
    updates: dict[str, str] = {
        "auto_ingest_enabled": enabled_value,
        "ingest_cleanup_policy": body.cleanup_policy,
    }
    if body.default_delay_minutes is not None:
        if body.default_delay_minutes < 0:
            raise HTTPException(status_code=400, detail="default_delay_minutes must be non-negative.")
        updates["ingest_default_delay_minutes"] = str(body.default_delay_minutes)
    for key, value in updates.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )

    action = "enable" if body.enabled else "disable"
    await log_action(
        session,
        user_id=user_id,
        entity_type="auto_ingest",
        entity_id=0,
        action=action,
        details={"cleanup_policy": body.cleanup_policy},
    )

    await session.commit()
    return {"status": "ok", "enabled": body.enabled, "cleanup_policy": body.cleanup_policy}


@router.get("/api/v1/settings/auto-ingest", response_model=AutoIngestStatus)
async def get_auto_ingest_status(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
) -> AutoIngestStatus:
    """Return current auto-ingest status including message counts."""
    keys = [
        "auto_ingest_enabled",
        "ingest_cleanup_policy",
        "ingest_default_delay_minutes",
        "pubsub_topic_name",
        "pubsub_subscription_name",
    ]
    rows = (
        await session.execute(
            text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=keys)
        )
    ).fetchall()
    config = {r[0]: r[1] for r in rows}

    enabled = config.get("auto_ingest_enabled", "false") == "true"
    cleanup_policy = config.get("ingest_cleanup_policy", "delete_after_copy")
    delay_str = config.get("ingest_default_delay_minutes", "15")
    default_delay_minutes = int(delay_str) if delay_str and delay_str != "null" else 15
    topic = config.get("pubsub_topic_name")
    subscription = config.get("pubsub_subscription_name")
    if topic == "null":
        topic = None
    if subscription == "null":
        subscription = None

    # Check listener state
    from app.services.pubsub_listener import get_listener

    listener = get_listener()
    listener_running = listener.running if listener else False

    # Count messages in last 24 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    processed_result = await session.execute(
        select(func.count(IngestEvent.id)).where(
            IngestEvent.created_at >= cutoff,
            IngestEvent.ingest_status != "failed",
        )
    )
    processed_24h = processed_result.scalar() or 0

    failed_result = await session.execute(
        select(func.count(IngestEvent.id)).where(
            IngestEvent.created_at >= cutoff,
            IngestEvent.ingest_status == "failed",
        )
    )
    failed_24h = failed_result.scalar() or 0

    return AutoIngestStatus(
        enabled=enabled,
        cleanup_policy=cleanup_policy,
        default_delay_minutes=default_delay_minutes,
        listener_running=listener_running,
        pubsub_topic=topic,
        pubsub_subscription=subscription,
        messages_processed_24h=processed_24h,
        messages_failed_24h=failed_24h,
    )
