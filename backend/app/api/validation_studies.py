"""HTTP API for the literature-validation flow (lit_validation).

Thin glue over the services: request a study, run read-and-plan (B1 text -> B2/B3 extraction ->
plan_ready or an early-exit classification), then the C1 gate (approve/decline). RBAC via the
``lit_validation`` permission. The comparison/execution back half is not wired here yet.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.validation_study import ValidationStudy
from app.schemas.validation_study import (
    ComparisonTargetResponse,
    DeclineRequest,
    ReadRequest,
    ReproductionPlanResponse,
    ValidationStudyRequest,
    ValidationStudyResponse,
)
from app.services.reproduction_plan_service import ReproductionPlanService
from app.services.validation_driver_service import ValidationDriverService
from app.services.validation_study_service import ValidationStudyService

router = APIRouter(prefix="/api/validation-studies", tags=["validation-studies"])


def _plan_response(plan) -> ReproductionPlanResponse | None:
    if plan is None:
        return None
    return ReproductionPlanResponse(
        id=plan.id,
        accessions=plan.accessions_json,
        sample_sheet=plan.sample_sheet_json,
        pipeline_key=plan.pipeline_key,
        pipeline_version=plan.pipeline_version,
        parameters=plan.parameters_json,
        reference_genome=plan.reference_genome,
        reference_build=plan.reference_build,
        mapping_confidence=plan.mapping_confidence,
        mapping_notes=plan.mapping_notes,
        blockers=plan.blockers_json,
        extractor_model=plan.extractor_model,
        extractor_provider=plan.extractor_provider,
        comparison_targets=[
            ComparisonTargetResponse(
                metric_key=t.metric_key,
                claimed_value=t.claimed_value,
                unit=t.unit,
                tolerance=t.tolerance,
                source_locator=t.source_locator,
            )
            for t in (plan.comparison_targets or [])
        ],
    )


async def _study_response(session: AsyncSession, study: ValidationStudy, org_id: int) -> ValidationStudyResponse:
    plan = await ReproductionPlanService.get_plan(session, study.id, org_id)
    return ValidationStudyResponse(
        id=study.id,
        state=study.state,
        classification=study.classification,
        paper_id=study.paper_id,
        source_doi=study.source_doi,
        source_accession=study.source_accession,
        experiment_id=study.experiment_id,
        reproduction_plan_id=study.reproduction_plan_id,
        approved_by_user_id=study.approved_by_user_id,
        failure_reason=study.failure_reason,
        plan=_plan_response(plan),
    )


async def _load(session: AsyncSession, study_id: int, org_id: int) -> ValidationStudy:
    study = (
        await session.execute(
            select(ValidationStudy).where(
                ValidationStudy.id == study_id,
                ValidationStudy.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if not study:
        raise HTTPException(404, "Validation study not found")
    return study


@router.post("", response_model=ValidationStudyResponse)
async def request_validation(
    data: ValidationStudyRequest,
    current_user: dict = require_permission("lit_validation", "request"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.create_study(
        session,
        org_id,
        user_id,
        paper_id=data.paper_id,
        source_doi=data.source_doi,
        source_accession=data.source_accession,
    )
    await session.commit()
    return await _study_response(session, study, org_id)


@router.get("/{study_id}", response_model=ValidationStudyResponse)
async def get_study(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    study = await _load(session, study_id, org_id)
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/read", response_model=ValidationStudyResponse)
async def read_and_plan(
    study_id: int,
    data: ReadRequest,
    current_user: dict = require_permission("lit_validation", "request"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await _load(session, study_id, org_id)
    study = await ValidationDriverService.read_and_plan(session, study, data.full_text, org_id, user_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/approve", response_model=ValidationStudyResponse)
async def approve_plan(
    study_id: int,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.approve_plan(session, study_id, org_id, user_id)
    await session.commit()
    return await _study_response(session, study, org_id)


@router.post("/{study_id}/decline", response_model=ValidationStudyResponse)
async def decline_plan(
    study_id: int,
    data: DeclineRequest,
    current_user: dict = require_permission("lit_validation", "approve"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    study = await ValidationStudyService.decline_plan(session, study_id, org_id, user_id, reason=data.reason)
    await session.commit()
    return await _study_response(session, study, org_id)
