"""Per-org Literature Source configuration: enable/disable, set API key,
override rate limit. Audit all writes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.literature import EXTERNAL_SOURCES, LiteratureSourcesConfig
from app.services import audit_service


class SourceNotFound(Exception):
    pass


class UnknownSource(Exception):
    pass


async def list_for_org(session: AsyncSession, org_id: int) -> list[LiteratureSourcesConfig]:
    result = await session.execute(
        select(LiteratureSourcesConfig)
        .where(LiteratureSourcesConfig.organization_id == org_id)
        .order_by(LiteratureSourcesConfig.source)
    )
    return list(result.scalars().all())


async def get_or_create(session: AsyncSession, org_id: int, source: str) -> LiteratureSourcesConfig:
    if source not in EXTERNAL_SOURCES:
        raise UnknownSource(f"unknown source: {source}")
    result = await session.execute(
        select(LiteratureSourcesConfig).where(
            LiteratureSourcesConfig.organization_id == org_id,
            LiteratureSourcesConfig.source == source,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = LiteratureSourcesConfig(organization_id=org_id, source=source, enabled=True)
    session.add(row)
    await session.flush()
    return row


async def update(
    session: AsyncSession,
    *,
    org_id: int,
    source: str,
    user_id: int,
    enabled: bool | None = None,
    api_key: str | None = None,
    rate_limit_override: int | None = None,
    api_key_id: int | None = None,
) -> LiteratureSourcesConfig:
    row = await get_or_create(session, org_id, source)
    previous = {
        "enabled": row.enabled,
        "rate_limit_override": row.rate_limit_override,
        "has_api_key": bool(row.api_key),
    }
    if enabled is not None:
        row.enabled = enabled
    if api_key is not None:
        row.api_key = api_key or None
    if rate_limit_override is not None:
        row.rate_limit_override = rate_limit_override or None
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type="literature_sources_config",
        entity_id=row.id,
        action="update",
        details={
            "source": source,
            "enabled": row.enabled,
            "rate_limit_override": row.rate_limit_override,
            "api_key_set": api_key is not None,
        },
        previous_value=previous,
    )
    return row


async def test_connection(source: str, api_key: str | None) -> dict:
    """Run a trivial query against the source to confirm reachability. Returns
    {success, message, latency_ms}."""
    import time

    from app.services.literature.sources import get_adapter

    if source not in EXTERNAL_SOURCES:
        raise UnknownSource(f"unknown source: {source}")
    adapter = get_adapter(source)
    started = time.monotonic()
    try:
        results = await adapter.search("test", 1, api_key)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "success": True,
            "message": f"Source reachable; returned {len(results)} result(s)",
            "latency_ms": latency_ms,
        }
    except Exception as e:  # pragma: no cover
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"success": False, "message": str(e)[:200], "latency_ms": latency_ms}
