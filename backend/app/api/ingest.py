from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.experiment import Experiment
from app.models.file import File
from app.models.ingest_event import IngestEvent
from app.models.project import Project
from app.models.sample import Sample
from app.schemas.ingest import (
    BulkReassignRequest,
    BulkReassignResponse,
    ClaimRequest,
    IngestEventResponse,
    IngestSimulateRequest,
    UnclaimedEntityResponse,
)
from app.services.audit_service import log_action
from app.services.ingest_service import get_unclaimed_entities, process_ingest_event

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _event_response(e) -> IngestEventResponse:
    return IngestEventResponse(
        id=e.id,
        file_id=e.file_id,
        source_bucket=e.source_bucket,
        source_path=e.source_path,
        naming_profile_id=e.naming_profile_id,
        parsed_project_code=e.parsed_project_code,
        parsed_experiment_code=e.parsed_experiment_code,
        parsed_sample_id=e.parsed_sample_id,
        resolved_project_id=e.resolved_project_id,
        resolved_experiment_id=e.resolved_experiment_id,
        resolved_sample_id=e.resolved_sample_id,
        auto_created_entities=e.auto_created_entities,
        ingest_status=e.ingest_status,
        error_message=e.error_message,
        created_at=e.created_at,
    )


@router.get("/events", response_model=list[IngestEventResponse])
async def list_ingest_events(
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    query = select(IngestEvent).order_by(IngestEvent.created_at.desc()).limit(limit)
    if status:
        query = query.where(IngestEvent.ingest_status == status)
    result = await session.execute(query)
    return [_event_response(e) for e in result.scalars().all()]


@router.get("/events/{event_id}", response_model=IngestEventResponse)
async def get_ingest_event(
    event_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(IngestEvent).where(IngestEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Ingest event not found")
    return _event_response(event)


@router.post("/simulate", response_model=IngestEventResponse)
async def simulate_ingest(
    body: IngestSimulateRequest,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Simulate a file arrival for local/POC testing."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    event = await process_ingest_event(
        filename=body.filename,
        source_bucket="bioaf-ingest-local",
        source_path=f"simulate/{body.filename}",
        org_id=org_id,
        db=session,
        user_id=user_id,
        file_size_bytes=body.file_size_bytes,
        content_md5=body.content_md5,
    )
    await session.commit()
    # Re-fetch to ensure all fields populated
    result = await session.execute(select(IngestEvent).where(IngestEvent.id == event.id))
    event = result.scalar_one_or_none()
    return _event_response(event)


@router.post("/reassign", response_model=BulkReassignResponse)
async def bulk_reassign(
    body: BulkReassignRequest,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    """Bulk reassign files to a different project, experiment, or sample."""
    user_id = int(current_user["sub"])
    reassigned = []
    for file_id in body.file_ids:
        result = await session.execute(select(File).where(File.id == file_id))
        file = result.scalar_one_or_none()
        if not file:
            continue

        previous = {"project_id": file.project_id}
        if body.target_project_id is not None:
            file.project_id = body.target_project_id

        await session.flush()
        await log_action(
            session,
            user_id=user_id,
            entity_type="file",
            entity_id=file.id,
            action="reassign",
            details={
                "target_project_id": body.target_project_id,
                "target_experiment_id": body.target_experiment_id,
                "target_sample_id": body.target_sample_id,
            },
            previous_value=previous,
        )
        reassigned.append(file_id)

    await session.commit()
    return BulkReassignResponse(reassigned_count=len(reassigned), file_ids=reassigned)


@router.get("/unmatched", response_model=list[IngestEventResponse])
async def list_unmatched(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(IngestEvent).where(IngestEvent.ingest_status == "unmatched").order_by(IngestEvent.created_at.desc())
    )
    return [_event_response(e) for e in result.scalars().all()]


@router.get("/unclaimed", response_model=list[UnclaimedEntityResponse])
async def list_unclaimed(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    entities = await get_unclaimed_entities(org_id, session)
    return [UnclaimedEntityResponse(**e) for e in entities]


# --- Claim endpoints on existing entity routers ---

claim_router = APIRouter(tags=["ingest"])


@claim_router.post("/api/projects/{project_id}/claim")
async def claim_project(
    project_id: int,
    body: ClaimRequest,
    current_user: dict = require_permission("projects", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")
    if not project.is_unclaimed:
        raise HTTPException(400, "Project is not unclaimed")

    project.is_unclaimed = False
    project.owner_user_id = user_id
    if body.name:
        project.name = body.name
    if body.description:
        project.description = body.description
    await session.flush()

    await log_action(session, user_id=user_id, entity_type="project", entity_id=project.id, action="claim")
    await session.commit()
    return {"id": project.id, "name": project.name, "is_unclaimed": False}


@claim_router.post("/api/experiments/{experiment_id}/claim")
async def claim_experiment(
    experiment_id: int,
    body: ClaimRequest,
    current_user: dict = require_permission("experiments", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    result = await session.execute(select(Experiment).where(Experiment.id == experiment_id))
    experiment = result.scalar_one_or_none()
    if not experiment:
        raise HTTPException(404, "Experiment not found")
    if not experiment.is_unclaimed:
        raise HTTPException(400, "Experiment is not unclaimed")

    experiment.is_unclaimed = False
    experiment.owner_user_id = user_id
    if body.name:
        experiment.name = body.name
    if body.description:
        experiment.description = body.description
    await session.flush()

    await log_action(session, user_id=user_id, entity_type="experiment", entity_id=experiment.id, action="claim")
    await session.commit()
    return {"id": experiment.id, "name": experiment.name, "is_unclaimed": False}


@claim_router.post("/api/samples/{sample_id}/claim")
async def claim_sample(
    sample_id: int,
    body: ClaimRequest,
    current_user: dict = require_permission("samples", "edit"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    result = await session.execute(select(Sample).where(Sample.id == sample_id))
    sample = result.scalar_one_or_none()
    if not sample:
        raise HTTPException(404, "Sample not found")
    if not sample.is_unclaimed:
        raise HTTPException(400, "Sample is not unclaimed")

    sample.is_unclaimed = False
    if body.name:
        sample.sample_id_unique = body.name
    await session.flush()

    await log_action(session, user_id=user_id, entity_type="sample", entity_id=sample.id, action="claim")
    await session.commit()
    return {"id": sample.id, "sample_id_unique": sample.sample_id_unique, "is_unclaimed": False}
