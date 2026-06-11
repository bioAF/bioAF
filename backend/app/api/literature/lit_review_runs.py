"""Lit Review Run endpoints: trigger and read on-demand reviews for Experiments."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.literature import CreateLitReviewRunRequest, LitReviewRunListResponse, LitReviewRunPayload
from app.services.literature import lit_review_run_service

router = APIRouter()


def _serialize_run(r) -> LitReviewRunPayload:
    return LitReviewRunPayload(
        id=r.id,
        experiment_id=r.experiment_id,
        triggered_by_user_id=r.triggered_by_user_id,
        status=r.status,
        llm_provider=r.llm_provider,
        llm_model=r.llm_model,
        expansion_queries_json=r.expansion_queries_json,
        candidate_count=r.candidate_count,
        recommendation_count=r.recommendation_count,
        max_recommendations=r.max_recommendations,
        score_threshold=r.score_threshold,
        started_at=r.started_at,
        completed_at=r.completed_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


@router.post(
    "/experiments/{experiment_id}/lit-review-runs",
    response_model=LitReviewRunPayload,
    status_code=201,
)
async def create_lit_review_run_endpoint(
    experiment_id: int,
    body: CreateLitReviewRunRequest = Body(default_factory=CreateLitReviewRunRequest),
    current_user: dict = require_permission("literature", "run_lit_review"),
    session: AsyncSession = Depends(get_session),
):
    try:
        run = await lit_review_run_service.create_run(
            session,
            org_id=int(current_user["org_id"]),
            experiment_id=experiment_id,
            triggered_by_user_id=int(current_user["sub"]),
            max_recommendations=body.max_recommendations,
            score_threshold=body.score_threshold,
        )
    except lit_review_run_service.NoActiveLlmProvider:
        raise HTTPException(409, "no_active_llm_provider")
    except lit_review_run_service.ReviewRunFailed as e:
        raise HTTPException(400, str(e))
    await session.commit()
    await lit_review_run_service.schedule_run(run_id=run.id)
    return _serialize_run(run)


@router.get(
    "/experiments/{experiment_id}/lit-review-runs",
    response_model=LitReviewRunListResponse,
)
async def list_lit_review_runs_endpoint(
    experiment_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    rows = await lit_review_run_service.list_runs_for_experiment(
        session, org_id=int(current_user["org_id"]), experiment_id=experiment_id
    )
    return LitReviewRunListResponse(items=[_serialize_run(r) for r in rows])


@router.get("/lit-review-runs/{run_id}", response_model=LitReviewRunPayload)
async def get_lit_review_run_endpoint(
    run_id: int,
    current_user: dict = require_permission("literature", "view"),
    session: AsyncSession = Depends(get_session),
):
    row = await lit_review_run_service.get_run(session, org_id=int(current_user["org_id"]), run_id=run_id)
    if row is None:
        raise HTTPException(404, "lit review run not found")
    return _serialize_run(row)
