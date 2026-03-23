from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.pipeline_run_review import (
    PipelineRunReviewCreate,
    PipelineRunReviewListResponse,
    PipelineRunReviewResponse,
    ReviewerSummary,
)
from app.services.pipeline_review_service import PipelineReviewService

router = APIRouter(prefix="/api/pipeline-runs", tags=["pipeline-run-reviews"])


def _review_response(review) -> PipelineRunReviewResponse:
    reviewer = None
    if review.reviewer:
        reviewer = ReviewerSummary(
            id=review.reviewer.id,
            name=review.reviewer.name,
            email=review.reviewer.email,
        )
    return PipelineRunReviewResponse(
        id=review.id,
        pipeline_run_id=review.pipeline_run_id,
        reviewer=reviewer,
        verdict=review.verdict,
        notes=review.notes,
        sample_verdicts_json=review.sample_verdicts_json,
        recommended_exclusions=review.recommended_exclusions,
        reviewed_at=review.reviewed_at,
        is_active=review.superseded_by_id is None,
        created_at=review.created_at,
    )


@router.post("/{run_id}/reviews", response_model=PipelineRunReviewResponse, status_code=201)
async def create_review(
    run_id: int,
    body: PipelineRunReviewCreate,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Submit a review for a pipeline run."""
    user_id = int(current_user["sub"])

    # Convert sample_verdicts from Pydantic models to dicts
    sample_verdicts_dict = None
    if body.sample_verdicts:
        sample_verdicts_dict = {k: v.model_dump() for k, v in body.sample_verdicts.items()}

    try:
        review = await PipelineReviewService.create_review(
            session=session,
            pipeline_run_id=run_id,
            reviewer_user_id=user_id,
            verdict=body.verdict,
            notes=body.notes,
            sample_verdicts=sample_verdicts_dict,
            recommended_exclusions=body.recommended_exclusions,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(404, str(e))

    # Reload with reviewer relationship
    review = await PipelineReviewService.get_active_review(session, run_id)
    return _review_response(review)


@router.get("/{run_id}/reviews", response_model=PipelineRunReviewListResponse)
async def list_reviews(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """List all reviews for a pipeline run, newest first."""
    reviews = await PipelineReviewService.list_reviews(session, run_id)
    return PipelineRunReviewListResponse(reviews=[_review_response(r) for r in reviews])


@router.get("/{run_id}/review", response_model=PipelineRunReviewResponse)
async def get_active_review(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Get the active (non-superseded) review for a pipeline run."""
    review = await PipelineReviewService.get_active_review(session, run_id)
    if not review:
        raise HTTPException(404, "No active review found for this pipeline run")
    return _review_response(review)
