"""Agent review API (ADR-055, spec-llm-integration-ui).

Three permission tiers:
- llm_integration:use to trigger a review (POST /run) or dismiss/undismiss.
- llm_integration:use to view in the tab; viewer-role users without :use see
  the tab read-only via the inherited entity:view permission on the parent
  pipeline_run or experiment (handled by the frontend; the API gates writes
  but allows reads to any role with view on the parent entity).
- The job is the operational record; only agent_review rows are exposed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session, async_session_factory
from app.models.agent_review import AgentReview
from app.models.pipeline_run import PipelineRun
from app.services import agent_review_job_service as job_service
from app.services.agent_review_job_service import (
    JobAlreadyRunning,
    NoActiveProvider,
)

router = APIRouter(prefix="/api/agent_reviews", tags=["agent-reviews"])


class RunReviewRequest(BaseModel):
    entity_type: Literal["pipeline_run", "experiment"]
    entity_id: int
    review_type: Literal["pipeline_run_review_v1", "experiment_run_comparison_v1"]
    included_run_ids: list[int] | None = None
    include_html_report_run_ids: list[int] | None = None


class RunReviewResponse(BaseModel):
    job_id: int
    agent_review_id: int


class AgentReviewSummary(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    included_run_ids: list[int] | None
    review_type: str
    provider: str
    model: str
    status: str
    severity: str | None
    headline: str | None
    stale: bool
    dismissed: bool
    created_at: datetime
    completed_at: datetime | None


class AgentReviewDetail(AgentReviewSummary):
    flags: list[dict] | None
    evidence: list[str] | None
    body: str | None
    error_text: str | None
    artifact_gcs_paths: list[str]
    dismissed_at: datetime | None
    dismissed_by_user_id: int | None


class AgentReviewListResponse(BaseModel):
    items: list[AgentReviewSummary]


async def _is_stale(session: AsyncSession, review: AgentReview) -> bool:
    if review.entity_type != "experiment":
        return False
    included = set(review.included_run_ids or [])
    rows = (
        (await session.execute(select(PipelineRun.id).where(PipelineRun.experiment_id == review.entity_id)))
        .scalars()
        .all()
    )
    return any(rid not in included for rid in rows)


async def _to_summary(session: AsyncSession, review: AgentReview) -> AgentReviewSummary:
    return AgentReviewSummary(
        id=review.id,
        entity_type=review.entity_type,
        entity_id=review.entity_id,
        included_run_ids=review.included_run_ids,
        review_type=review.review_type,
        provider=review.provider,
        model=review.model,
        status=review.status,
        severity=review.severity,
        headline=review.headline,
        stale=await _is_stale(session, review),
        dismissed=review.dismissed_at is not None,
        created_at=review.created_at,
        completed_at=review.completed_at,
    )


@router.post("/run", response_model=RunReviewResponse, status_code=202)
async def run_review(
    body: RunReviewRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        job, review = await job_service.create(
            session,
            org_id=org_id,
            user_id=user_id,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            review_type=body.review_type,
            included_run_ids=body.included_run_ids,
            include_html_report_run_ids=body.include_html_report_run_ids,
        )
        await session.commit()
    except NoActiveProvider as exc:
        raise HTTPException(412, str(exc))
    except JobAlreadyRunning as exc:
        raise HTTPException(
            409,
            detail={
                "detail": "review_in_progress",
                "existing_job_id": exc.existing_job_id,
                "existing_agent_review_id": exc.existing_agent_review_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if job.provider == "gemma":
        # Gemma path is dispatched through the pipeline orchestrator.
        # v1 stubs the dispatch by leaving the job in 'pending'; the
        # orchestrator integration lands as part of the work-nodes roadmap.
        # The card surfaces as pending until the orchestrator wakes it up.
        return RunReviewResponse(job_id=job.id, agent_review_id=review.id)

    background_tasks.add_task(
        job_service.execute_hosted,
        async_session_factory,
        job_id=job.id,
    )
    return RunReviewResponse(job_id=job.id, agent_review_id=review.id)


@router.get("", response_model=AgentReviewListResponse)
async def list_reviews(
    entity_type: Literal["pipeline_run", "experiment"] = Query(...),
    entity_id: int = Query(...),
    filter: Literal["active", "dismissed", "stale", "failed"] = Query("active"),
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # The Pipeline Run tab shows single-run reviews of X UNION experiment-level
    # reviews that included X. The Experiment tab shows only experiment-level
    # reviews of the experiment id.
    if entity_type == "pipeline_run":
        run_match = (AgentReview.entity_type == "pipeline_run") & (AgentReview.entity_id == entity_id)
        exp_match = (AgentReview.entity_type == "experiment") & (AgentReview.included_run_ids.contains([entity_id]))
        clause = run_match | exp_match
    else:
        clause = (AgentReview.entity_type == "experiment") & (AgentReview.entity_id == entity_id)

    rows = (
        (
            await session.execute(
                select(AgentReview)
                .where(AgentReview.organization_id == org_id, clause)
                .order_by(AgentReview.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    summaries: list[AgentReviewSummary] = []
    for r in rows:
        summary = await _to_summary(session, r)
        if filter == "active" and summary.dismissed:
            continue
        if filter == "dismissed" and not summary.dismissed:
            continue
        if filter == "stale" and not summary.stale:
            continue
        if filter == "failed" and summary.status != "failed":
            continue
        summaries.append(summary)

    return AgentReviewListResponse(items=summaries)


@router.get("/{review_id}", response_model=AgentReviewDetail)
async def get_review(
    review_id: int,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    review = (
        await session.execute(
            select(AgentReview).where(AgentReview.id == review_id, AgentReview.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(404, "review not found")
    summary = await _to_summary(session, review)
    return AgentReviewDetail(
        **summary.model_dump(),
        flags=review.flags,
        evidence=review.evidence,
        body=review.body,
        error_text=review.error_text,
        artifact_gcs_paths=list(review.artifact_gcs_paths or []),
        dismissed_at=review.dismissed_at,
        dismissed_by_user_id=review.dismissed_by_user_id,
    )


@router.post("/{review_id}/dismiss", status_code=204)
async def dismiss_review(
    review_id: int,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    review = (
        await session.execute(
            select(AgentReview).where(AgentReview.id == review_id, AgentReview.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(404, "review not found")
    review.dismissed_at = datetime.now(UTC)
    review.dismissed_by_user_id = user_id
    await session.commit()


@router.post("/{review_id}/undismiss", status_code=204)
async def undismiss_review(
    review_id: int,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    review = (
        await session.execute(
            select(AgentReview).where(AgentReview.id == review_id, AgentReview.organization_id == org_id)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(404, "review not found")
    review.dismissed_at = None
    review.dismissed_by_user_id = None
    await session.commit()
