"""Per-org Literature Source configuration: enable/disable, set API key,
override rate limit. Audit all writes."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.literature import EXTERNAL_SOURCES, LiteratureSourcesConfig
from app.services import audit_service
from app.services.literature.source_policy import SourceExecution, effective_rate

logger = logging.getLogger("bioaf.literature.sources_config")


class _Unset:
    pass


_UNSET = _Unset()


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


async def list_enabled(session: AsyncSession, org_id: int) -> list[LiteratureSourcesConfig]:
    """The org's currently enabled sources. Every AI Literature Review boundary
    (creation, execution, scheduled sweep) reads configuration through here."""
    return [row for row in await list_for_org(session, org_id) if row.enabled]


async def resolve(session: AsyncSession, org_id: int, sources: list[str]) -> dict[str, SourceExecution]:
    """Snapshot each source's enabled state, credential, and effective rate for
    one operation. A source with no configuration row resolves enabled at its
    defaults (bootstrap seeds every source; the row default is enabled). A
    persisted override that is not a positive integer resolves to the default."""
    unknown = [s for s in sources if s not in EXTERNAL_SOURCES]
    if unknown:
        raise UnknownSource(f"unknown sources: {unknown}")
    rows = {
        row.source: row
        for row in (
            await session.execute(
                select(LiteratureSourcesConfig).where(
                    LiteratureSourcesConfig.organization_id == org_id,
                    LiteratureSourcesConfig.source.in_(sources),
                )
            )
        ).scalars()
    }
    resolved: dict[str, SourceExecution] = {}
    for source in sources:
        row = rows.get(source)
        api_key = (row.api_key or None) if row is not None else None
        raw = row.rate_limit_override if row is not None else None
        override = raw if isinstance(raw, int) and raw > 0 else None
        resolved[source] = SourceExecution(
            source=source,
            enabled=row.enabled if row is not None else True,
            api_key=api_key,
            requests_per_second=effective_rate(source, bool(api_key), override),
            override=override,
        )
    return resolved


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
    rate_limit_override: int | None | _Unset = _UNSET,
    api_key_id: int | None = None,
) -> LiteratureSourcesConfig:
    """Omitted ``rate_limit_override`` leaves it unchanged; None or 0 resets it;
    a negative value is refused."""
    if isinstance(rate_limit_override, int) and rate_limit_override < 0:
        raise ValidationError("rate_limit_override must be a positive number of requests per second")
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
    if not isinstance(rate_limit_override, _Unset):
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


def _safe_failure_message(exc: Exception) -> str:
    """A fixed diagnostic for a failed connection test. Exception text is never
    echoed: source clients put credentials (PubMed's api_key) in request URLs,
    and httpx includes the URL in its error messages."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "Source rate limited the request (HTTP 429)"
        return f"Source returned HTTP {status}"
    if isinstance(exc, httpx.TimeoutException):
        return "Source timed out"
    if isinstance(exc, httpx.TransportError):
        return "Could not reach source"
    if isinstance(exc, ValueError | ET.ParseError):
        return "Source returned an unreadable response"
    return f"Source test failed ({type(exc).__name__})"


async def test_connection(source: str, api_key: str | None) -> dict:
    """Run a trivial query against the source to confirm reachability. Returns
    {success, message, latency_ms}."""
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
    except Exception as e:
        latency_ms = int((time.monotonic() - started) * 1000)
        message = _safe_failure_message(e)
        logger.warning("Literature source test failed: source=%s failure=%s", source, type(e).__name__)
        return {"success": False, "message": message, "latency_ms": latency_ms}
