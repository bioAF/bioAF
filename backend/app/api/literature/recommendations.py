"""Recommendation endpoints: list AI recommendations and accept/dismiss them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.api.literature._common import _serialize_paper
from app.database import get_session
from app.schemas.literature import RecommendationListResponse, RecommendationPayload
from app.services.literature import paper_service, recommendation_service

router = APIRouter()


async def _serialize_recommendation(session: AsyncSession, rec, user_id: int) -> RecommendationPayload:
    paper = await paper_service.get_paper(session, rec.organization_id, rec.paper_id)
    return RecommendationPayload(
        id=rec.id,
        paper=await _serialize_paper(session, paper, user_id),
        experiment_id=rec.experiment_id,
        review_run_id=rec.review_run_id,
        relevance_score=rec.relevance_score,
        relevance_bucket=rec.relevance_bucket,
        reasoning=rec.reasoning,
        status=rec.status,
        decided_by_user_id=rec.decided_by_user_id,
        decided_at=rec.decided_at,
        created_at=rec.created_at,
    )


@router.get("/recommendations", response_model=RecommendationListResponse)
async def list_recommendations_endpoint(
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
    experiment_id: int | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    rows, total = await recommendation_service.list_for_org(
        session,
        org_id=int(current_user["org_id"]),
        experiment_id=experiment_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    user_id = int(current_user["sub"])
    items = [await _serialize_recommendation(session, r, user_id) for r in rows]
    return RecommendationListResponse(items=items, total=total)


@router.post("/recommendations/{recommendation_id}/accept", response_model=RecommendationPayload)
async def accept_recommendation_endpoint(
    recommendation_id: int,
    current_user: dict = require_permission("literature", "run_lit_review"),
    session: AsyncSession = Depends(get_session),
):
    try:
        rec = await recommendation_service.accept(
            session,
            org_id=int(current_user["org_id"]),
            recommendation_id=recommendation_id,
            user_id=int(current_user["sub"]),
        )
    except recommendation_service.RecommendationNotFound:
        raise HTTPException(404, "recommendation not found")
    except recommendation_service.RecommendationAlreadyDecided as e:
        raise HTTPException(409, str(e))
    await session.commit()
    return await _serialize_recommendation(session, rec, int(current_user["sub"]))


@router.post("/recommendations/{recommendation_id}/dismiss", response_model=RecommendationPayload)
async def dismiss_recommendation_endpoint(
    recommendation_id: int,
    current_user: dict = require_permission("literature", "dismiss"),
    session: AsyncSession = Depends(get_session),
):
    try:
        rec = await recommendation_service.dismiss(
            session,
            org_id=int(current_user["org_id"]),
            recommendation_id=recommendation_id,
            user_id=int(current_user["sub"]),
        )
    except recommendation_service.RecommendationNotFound:
        raise HTTPException(404, "recommendation not found")
    except recommendation_service.RecommendationAlreadyDecided as e:
        raise HTTPException(409, str(e))
    await session.commit()
    return await _serialize_recommendation(session, rec, int(current_user["sub"]))
