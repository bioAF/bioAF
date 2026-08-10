"""Agent review API (ADR-055, spec-llm-integration-ui).

Two permission tiers:
- llm_integration:use to trigger a review (POST /run) or dismiss/undismiss,
  and to manage saved prompts / the section catalog.
- "View Results" (experiments:view OR pipelines:view) to read reviews (the list
  and detail endpoints). The QC report that surfaces these reviews is reachable
  by anyone who can view experiments or pipelines, so reads are gated to match;
  bench/viewer/leadership without llm_integration:use can read but not write.
- The job is the operational record; only agent_review rows are exposed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission, require_results_view
from app.database import get_session, async_session_factory
from app.models.agent_review import AgentReview
from app.models.pipeline_run import PipelineRun
from app.models.user import User
from app.services import (
    agent_review_job_service as job_service,
    agent_review_prompt_service,
    llm_provider_config_service,
)
from app.services.agent_review_job_service import (
    JobAlreadyRunning,
    NoActiveProvider,
)
from app.services.agent_review_prompt_builder import assemble_prompt
from app.services.agent_review_section_catalog import (
    SECTIONS,
    default_sub_item_ids,
)

router = APIRouter(prefix="/api/agent_reviews", tags=["agent-reviews"])


class RunReviewRequest(BaseModel):
    entity_type: Literal["pipeline_run", "experiment"]
    entity_id: int
    included_run_ids: list[int] | None = None
    include_html_report_run_ids: list[int] | None = None
    # Prompt source: exactly one of these three should be set. If multiple
    # are set, custom_prompt_id wins, then custom_prompt_body, then sub-items.
    selected_sub_item_ids: list[str] | None = None
    custom_prompt_id: int | None = None
    custom_prompt_body: str | None = None


class AssemblePromptRequest(BaseModel):
    entity_type: Literal["pipeline_run", "experiment"]
    selected_sub_item_ids: list[str]


class AssemblePromptResponse(BaseModel):
    body: str


class SubItemPayload(BaseModel):
    id: str
    label: str
    default_on: bool
    prompt_fragment: str


class SectionPayload(BaseModel):
    id: str
    label: str
    experiment_only: bool
    sub_items: list[SubItemPayload]


class SectionCatalogResponse(BaseModel):
    sections: list[SectionPayload]
    pipeline_run_defaults: list[str]
    experiment_defaults: list[str]


class SavedPromptPayload(BaseModel):
    id: int
    name: str
    body: str
    created_by_user_id: int
    created_by_user_label: str
    created_at: datetime


class SavedPromptsResponse(BaseModel):
    items: list[SavedPromptPayload]


class CreateSavedPromptRequest(BaseModel):
    name: str
    body: str


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
    prompt_source: str | None
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
    prompt_text: str | None = None
    prompt_sections: list[str] | None = None
    prompt_source: str | None = None
    prompt_custom_id: int | None = None


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
        prompt_source=review.prompt_source,
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
            included_run_ids=body.included_run_ids,
            include_html_report_run_ids=body.include_html_report_run_ids,
            selected_sub_item_ids=body.selected_sub_item_ids,
            custom_prompt_id=body.custom_prompt_id,
            custom_prompt_body=body.custom_prompt_body,
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


@router.get("/section_catalog", response_model=SectionCatalogResponse)
async def get_section_catalog(
    current_user: dict = require_permission("llm_integration", "use"),
):
    """Return the section/sub-item catalog and per-scope default selections."""
    sections = [
        SectionPayload(
            id=s.id,
            label=s.label,
            experiment_only=s.experiment_only,
            sub_items=[
                SubItemPayload(
                    id=si.id,
                    label=si.label,
                    default_on=si.default_on,
                    prompt_fragment=si.prompt_fragment,
                )
                for si in s.sub_items
            ],
        )
        for s in SECTIONS
    ]
    return SectionCatalogResponse(
        sections=sections,
        pipeline_run_defaults=default_sub_item_ids(experiment_scope=False),
        experiment_defaults=default_sub_item_ids(experiment_scope=True),
    )


@router.post("/assemble_prompt", response_model=AssemblePromptResponse)
async def assemble_prompt_preview(
    body: AssemblePromptRequest,
    current_user: dict = require_permission("llm_integration", "use"),
):
    """Render the prompt the section selection would produce, for the
    Display prompt modal. Does not start a job and writes no audit row."""
    text = assemble_prompt(
        experiment_scope=body.entity_type == "experiment",
        selected_sub_item_ids=body.selected_sub_item_ids,
    )
    return AssemblePromptResponse(body=text)


async def _saved_prompt_payload(session: AsyncSession, prompt) -> SavedPromptPayload:
    creator = (await session.execute(select(User).where(User.id == prompt.created_by_user_id))).scalar_one_or_none()
    label = getattr(creator, "name", None) or (creator.email if creator else "unknown user")
    return SavedPromptPayload(
        id=prompt.id,
        name=prompt.name,
        body=prompt.body,
        created_by_user_id=prompt.created_by_user_id,
        created_by_user_label=label or "unknown user",
        created_at=prompt.created_at,
    )


@router.get("/prompts", response_model=SavedPromptsResponse)
async def list_saved_prompts(
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    rows = await agent_review_prompt_service.list_for_org(session, org_id)
    items = [await _saved_prompt_payload(session, r) for r in rows]
    return SavedPromptsResponse(items=items)


@router.post("/prompts", response_model=SavedPromptPayload, status_code=201)
async def create_saved_prompt(
    body: CreateSavedPromptRequest,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    row = await agent_review_prompt_service.create(
        session,
        org_id=org_id,
        name=body.name,
        body=body.body,
        created_by_user_id=user_id,
    )
    await session.commit()
    return await _saved_prompt_payload(session, row)


@router.delete("/prompts/{prompt_id}", status_code=204)
async def delete_saved_prompt(
    prompt_id: int,
    current_user: dict = require_permission("llm_integration", "use"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    deleted = await agent_review_prompt_service.delete(session, org_id=org_id, prompt_id=prompt_id)
    await session.commit()
    if not deleted:
        raise HTTPException(404, "saved prompt not found")


@router.get("", response_model=AgentReviewListResponse)
async def list_reviews(
    entity_type: Literal["pipeline_run", "experiment"] = Query(...),
    entity_id: int = Query(...),
    filter: Literal["all", "active", "dismissed", "stale", "failed"] = Query("active"),
    current_user: dict = require_results_view(),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # Each tab is strictly scoped to its own entity_type. Earlier the Pipeline
    # Run tab unioned in experiment-level reviews via included_run_ids; user
    # feedback was that this conflated two distinct review surfaces. An
    # experiment-level review now appears only on the Experiment tab.
    clause = (AgentReview.entity_type == entity_type) & (AgentReview.entity_id == entity_id)

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


class AvailabilityResponse(BaseModel):
    enabled: bool


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    current_user: dict = require_results_view(),
    session: AsyncSession = Depends(get_session),
):
    """Whether AI Review can run for this org: an active provider exists, has a
    model, and is not the stubbed gemma. Readable by View Results so the QC
    report can decide whether to surface the AI Review trigger without needing
    the admin-only providers endpoint. Returns a boolean only (no secrets).
    Declared before /{review_id} so the static path is not shadowed."""
    org_id = int(current_user["org_id"])
    active = await llm_provider_config_service.get_active(session, org_id)
    enabled = active is not None and bool(active.model) and active.provider != "gemma"
    return AvailabilityResponse(enabled=enabled)


@router.get("/{review_id}", response_model=AgentReviewDetail)
async def get_review(
    review_id: int,
    current_user: dict = require_results_view(),
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
        prompt_text=review.prompt_text,
        prompt_sections=review.prompt_sections,
        prompt_custom_id=review.prompt_custom_id,
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
