"""Source configuration endpoints: per-org external source enable/keys/test."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.literature import EXTERNAL_SOURCES
from app.schemas.literature import (
    SourceConfigListResponse,
    SourceConfigPayload,
    SourceConfigUpdateRequest,
    SourceTestResponse,
)
from app.services.literature import sources_config_service

router = APIRouter()


@router.get("/sources", response_model=SourceConfigListResponse)
async def list_sources_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    rows = await sources_config_service.list_for_org(session, int(current_user["org_id"]))
    items = [
        SourceConfigPayload(
            source=row.source,
            enabled=row.enabled,
            has_api_key=bool(row.api_key),
            rate_limit_override=row.rate_limit_override,
            last_success_at=row.last_success_at,
            last_status=row.last_status,
        )
        for row in rows
    ]
    return SourceConfigListResponse(items=items)


@router.patch("/sources/{source}", response_model=SourceConfigPayload)
async def update_source_endpoint(
    source: str,
    body: SourceConfigUpdateRequest,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if source not in EXTERNAL_SOURCES:
        raise HTTPException(404, "unknown source")
    try:
        row = await sources_config_service.update(
            session,
            org_id=int(current_user["org_id"]),
            source=source,
            user_id=int(current_user["sub"]),
            enabled=body.enabled,
            api_key=body.api_key,
            rate_limit_override=body.rate_limit_override,
        )
    except sources_config_service.UnknownSource:
        raise HTTPException(404, "unknown source")
    await session.commit()
    return SourceConfigPayload(
        source=row.source,
        enabled=row.enabled,
        has_api_key=bool(row.api_key),
        rate_limit_override=row.rate_limit_override,
        last_success_at=row.last_success_at,
        last_status=row.last_status,
    )


@router.post("/sources/{source}/test", response_model=SourceTestResponse)
async def test_source_endpoint(
    source: str,
    current_user: dict = require_permission("literature", "configure_sources"),
    session: AsyncSession = Depends(get_session),
):
    if source not in EXTERNAL_SOURCES:
        raise HTTPException(404, "unknown source")
    row = await sources_config_service.get_or_create(session, int(current_user["org_id"]), source)
    result = await sources_config_service.test_connection(source, row.api_key)
    return SourceTestResponse(**result)
