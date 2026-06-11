"""Search endpoints: submit ad-hoc Literature Searches and read their results.

v1 polls ``GET /searches/{id}`` for status updates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.api.literature._common import _serialize_paper
from app.database import get_session
from app.schemas.literature import (
    PaperListResponse,
    PaperResponse,
    SearchListResponse,
    SearchPayload,
    SearchSubmitRequest,
)
from app.services import role_service
from app.services.literature import search_service

router = APIRouter()


def _serialize_search(s) -> SearchPayload:
    return SearchPayload(
        id=s.id,
        query_text=s.query_text,
        sources=list(s.sources_json or []),
        per_source_status=dict(s.per_source_status or {}),
        status=s.status,
        result_count=s.result_count,
        error_message=s.error_message,
        started_at=s.started_at,
        completed_at=s.completed_at,
        created_at=s.created_at,
    )


@router.post("/searches", response_model=SearchPayload, status_code=201)
async def submit_search_endpoint(
    body: SearchSubmitRequest,
    current_user: dict = require_permission("literature", "run_search"),
    session: AsyncSession = Depends(get_session),
):
    if not body.query or not body.query.strip():
        raise HTTPException(400, "query must not be empty")
    row = await search_service.create_search(
        session,
        org_id=int(current_user["org_id"]),
        user_id=int(current_user["sub"]),
        query=body.query.strip(),
        sources=body.sources,
        max_per_source=body.max_per_source,
    )
    await session.commit()
    await search_service.schedule_search_run(
        search_id=row.id, user_id=int(current_user["sub"]), max_per_source=body.max_per_source
    )
    return _serialize_search(row)


@router.get("/searches", response_model=SearchListResponse)
async def list_searches_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    user_id = int(current_user["sub"])
    # Admin / comp_bio see all searches; everyone else sees only their own.
    can_see_all = await role_service.has_permission(
        session, int(current_user["role_id"]), "literature", "configure_sources"
    )
    rows, total = await search_service.list_searches(
        session,
        org_id=int(current_user["org_id"]),
        user_id=None if can_see_all else user_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return SearchListResponse(items=[_serialize_search(r) for r in rows], total=total)


@router.get("/searches/{search_id}", response_model=SearchPayload)
async def get_search_endpoint(
    search_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await search_service.get_search(session, org_id=int(current_user["org_id"]), search_id=search_id)
    except search_service.SearchNotFound:
        raise HTTPException(404, "search not found")
    return _serialize_search(row)


@router.get("/searches/{search_id}/results", response_model=PaperListResponse)
async def get_search_results_endpoint(
    search_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        await search_service.get_search(session, org_id=int(current_user["org_id"]), search_id=search_id)
    except search_service.SearchNotFound:
        raise HTTPException(404, "search not found")
    pairs = await search_service.list_search_results(session, search_id=search_id)
    seen: set[int] = set()
    user_id = int(current_user["sub"])
    items: list[PaperResponse] = []
    for _, paper in pairs:
        if paper.id in seen:
            continue
        seen.add(paper.id)
        items.append(await _serialize_paper(session, paper, user_id))
    return PaperListResponse(items=items, total=len(items), page=1, page_size=len(items) or 1)
