from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.project import (
    ProjectCreate,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectSamplesAdd,
    ProjectUpdate,
)
from app.schemas.provenance import ProvenanceDAG
from app.services.project_service import ProjectService
from app.services.provenance_service import ProvenanceService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    request: Request,
    status: str | None = Query(None),
    owner_user_id: int | None = Query(None),
    search: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    results = await ProjectService.list_projects(
        session, org_id, status=status, owner_user_id=owner_user_id, search=search
    )
    projects = [ProjectResponse(**r) for r in results]
    return ProjectListResponse(projects=projects, total=len(projects))


@router.post("", response_model=ProjectResponse)
async def create_project(
    body: ProjectCreate,
    current_user: dict = require_permission("projects", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    project = await ProjectService.create_project(session, org_id, user_id, body)
    await session.commit()

    # Get counts for response
    sample_count = len(body.sample_ids) if body.sample_ids else 0
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        hypothesis=project.hypothesis,
        status=project.status,
        owner_user_id=project.owner_user_id,
        owner_name=None,
        sample_count=sample_count,
        experiment_count=0,
        pipeline_run_count=0,
        snapshot_count=0,
        created_at=project.created_at,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    detail = await ProjectService.get_project_detail(session, project_id, org_id)
    if not detail:
        raise HTTPException(404, "Project not found")

    return ProjectDetailResponse(**detail)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    current_user: dict = require_permission("projects", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Verify project exists and belongs to org
    project = await ProjectService.get_project(session, project_id, org_id)
    if not project:
        raise HTTPException(404, "Project not found")

    updated = await ProjectService.update_project(session, project_id, user_id, body)
    await session.commit()

    return ProjectResponse(
        id=updated.id,
        name=updated.name,
        description=updated.description,
        hypothesis=updated.hypothesis,
        status=updated.status,
        owner_user_id=updated.owner_user_id,
        owner_name=None,
        sample_count=0,
        experiment_count=0,
        pipeline_run_count=0,
        snapshot_count=0,
        created_at=updated.created_at,
    )


@router.post("/{project_id}/samples")
async def add_samples(
    project_id: int,
    body: ProjectSamplesAdd,
    current_user: dict = require_permission("projects", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Verify project exists
    project = await ProjectService.get_project(session, project_id, org_id)
    if not project:
        raise HTTPException(404, "Project not found")

    await ProjectService.add_samples(session, project_id, user_id, body)
    await session.commit()

    return {"status": "ok", "added": len(body.sample_ids)}


@router.delete("/{project_id}/samples/{sample_id}")
async def remove_sample(
    project_id: int,
    sample_id: int,
    current_user: dict = require_permission("projects", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    # Verify project exists
    project = await ProjectService.get_project(session, project_id, org_id)
    if not project:
        raise HTTPException(404, "Project not found")

    await ProjectService.remove_sample(session, project_id, sample_id, user_id)
    await session.commit()

    return {"status": "ok"}


@router.get("/{project_id}/provenance", response_model=ProvenanceDAG)
async def get_project_provenance(
    project_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    current_user = request.state.current_user
    org_id = int(current_user["org_id"])

    project = await ProjectService.get_project(session, project_id, org_id)
    if not project:
        raise HTTPException(404, "Project not found")

    return await ProvenanceService.build_project_provenance(session, project_id)
