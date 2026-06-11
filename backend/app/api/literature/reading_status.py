"""Reading-status endpoints: per-user unread/reading/read state for a Paper."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.literature import ReadingStatusRequest, ReadingStatusResponse
from app.services.literature import paper_service, reading_status_service
from app.services.literature.paper_service import PaperNotFound

router = APIRouter()


@router.get("/papers/{paper_id}/reading-status", response_model=ReadingStatusResponse)
async def get_reading_status_endpoint(
    paper_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    user_id = int(current_user["sub"])
    status = await reading_status_service.get_status(session, paper_id, user_id) or "unread"
    return ReadingStatusResponse(paper_id=paper_id, user_id=user_id, status=status)


@router.put("/papers/{paper_id}/reading-status", response_model=ReadingStatusResponse)
async def set_reading_status_endpoint(
    paper_id: int,
    body: ReadingStatusRequest,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    try:
        await paper_service.get_paper(session, org_id, paper_id)
    except PaperNotFound:
        raise HTTPException(404, "paper not found")
    user_id = int(current_user["sub"])
    row = await reading_status_service.set_status(session, paper_id=paper_id, user_id=user_id, status=body.status)
    await session.commit()
    return ReadingStatusResponse(paper_id=paper_id, user_id=user_id, status=row.status)
