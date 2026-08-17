"""Saving a samplesheet design and reading back the one that applies.

The service beneath this has existed since the mapping table shipped; nothing
called it, so no launch was ever offered a design back. This is that surface.

**Permissions follow the blast radius** (design section 4). Authoring an
experiment's mapping needs only the right to launch there, because it affects
that experiment alone. Promoting to the project follows project access.
Promoting to the organization requires ``samplesheet_mappings:promote_organization``,
which only an admin holds: that is the one rung where a decision reaches people
who did not choose it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.experiment import Experiment
from app.models.project import Project
from app.models.samplesheet_mapping import MAPPING_SCOPES
from app.schemas.samplesheet_mapping import (
    SamplesheetMappingResponse,
    SamplesheetMappingSaveRequest,
)
from app.services import role_service
from app.services.samplesheet_mapping_service import SamplesheetMappingService

router = APIRouter(prefix="/api/samplesheet-mappings", tags=["samplesheet-mappings"])


async def _owned_experiment(session: AsyncSession, org_id: int, experiment_id: int) -> Experiment:
    experiment = await session.scalar(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.organization_id == org_id)
    )
    if experiment is None:
        raise HTTPException(404, "Experiment not found")
    return experiment


async def _owned_project(session: AsyncSession, org_id: int, project_id: int) -> Project:
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.organization_id == org_id)
    )
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


async def _authorize_scope(session: AsyncSession, user: dict, data: SamplesheetMappingSaveRequest, org_id: int) -> None:
    """Refuse a rung the caller may not reach, before anything is written."""
    role_id = int(user["role_id"])

    if data.scope == "experiment":
        if data.experiment_id is None:
            raise HTTPException(422, "An experiment-scoped mapping needs an experiment_id")
        await _owned_experiment(session, org_id, data.experiment_id)
        return

    if data.scope == "project":
        if data.project_id is None:
            raise HTTPException(422, "A project-scoped mapping needs a project_id")
        await _owned_project(session, org_id, data.project_id)
        if not await role_service.has_permission(session, role_id, "projects", "edit"):
            raise HTTPException(403, "Promoting a mapping to a project requires project access")
        return

    if not await role_service.has_permission(session, role_id, "samplesheet_mappings", "promote_organization"):
        raise HTTPException(403, "Promoting a mapping to the organization requires an admin")


@router.get("", response_model=SamplesheetMappingResponse)
async def resolve_mapping(
    pipeline_key: str = Query(...),
    experiment_id: int | None = Query(None),
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    """The design that applies here, and the rung it came from.

    Naming the rung is the load-bearing half: an inherited organization-wide
    binding otherwise looks identical to one somebody set for this experiment.
    """
    org_id = int(current_user["org_id"])
    if experiment_id is not None:
        await _owned_experiment(session, org_id, experiment_id)

    mapping, scope = await SamplesheetMappingService.resolve(session, org_id, pipeline_key, experiment_id)
    if mapping is None:
        return SamplesheetMappingResponse(pipeline_key=pipeline_key)

    return SamplesheetMappingResponse(
        pipeline_key=pipeline_key,
        scope=scope,
        experiment_id=mapping.experiment_id,
        project_id=mapping.project_id,
        values=SamplesheetMappingService.flatten(mapping),
        bindings=SamplesheetMappingService.flatten_bindings(mapping),
        updated_at=mapping.updated_at,
    )


@router.post("", response_model=SamplesheetMappingResponse)
async def save_mapping(
    data: SamplesheetMappingSaveRequest,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    """Create or update THE mapping for this pipeline at this scope."""
    if data.scope not in MAPPING_SCOPES:
        raise HTTPException(422, f"Unknown mapping scope {data.scope!r}")

    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    await _authorize_scope(session, current_user, data, org_id)

    mapping = await SamplesheetMappingService.save(
        session,
        org_id,
        user_id,
        data.pipeline_key,
        data.scope,
        experiment_id=data.experiment_id,
        project_id=data.project_id,
        values=data.values,
        bindings=data.bindings,
    )
    await session.commit()
    # ``updated_at`` carries an onupdate, so the flush expires it and reading it
    # while building the response would try to load it outside the async
    # context. Refreshing also means the response reports the row as stored
    # rather than as sent.
    await session.refresh(mapping)

    return SamplesheetMappingResponse(
        pipeline_key=mapping.pipeline_key,
        scope=mapping.scope,
        experiment_id=mapping.experiment_id,
        project_id=mapping.project_id,
        values=SamplesheetMappingService.flatten(mapping),
        bindings=SamplesheetMappingService.flatten_bindings(mapping),
        updated_at=mapping.updated_at,
    )
