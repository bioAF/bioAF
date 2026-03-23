from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.pipeline import (
    PipelineAddRequest,
    PipelineCatalogListResponse,
    PipelineCatalogResponse,
    PipelineVersionUpdateRequest,
)
from app.services.pipeline_catalog_service import PipelineCatalogService

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _catalog_response(entry) -> PipelineCatalogResponse:
    return PipelineCatalogResponse(
        id=entry.id,
        pipeline_key=entry.pipeline_key,
        name=entry.name,
        description=entry.description,
        source_type=entry.source_type,
        source_url=entry.source_url,
        version=entry.version,
        parameter_schema=entry.schema_json,
        default_params=entry.default_params_json,
        is_builtin=entry.is_builtin,
        enabled=entry.enabled,
    )


@router.get("", response_model=PipelineCatalogListResponse)
async def list_pipelines(
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    # Initialize built-in pipelines on first access
    await PipelineCatalogService.initialize_builtin_pipelines(session, org_id)
    await session.commit()

    pipelines = await PipelineCatalogService.list_pipelines(session, org_id)
    return PipelineCatalogListResponse(
        pipelines=[_catalog_response(p) for p in pipelines],
        total=len(pipelines),
    )


@router.get("/{key:path}", response_model=PipelineCatalogResponse)
async def get_pipeline(
    key: str,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    pipeline = await PipelineCatalogService.get_pipeline(session, org_id, key)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")
    return _catalog_response(pipeline)


@router.post("/custom", response_model=PipelineCatalogResponse)
async def add_custom_pipeline(
    data: PipelineAddRequest,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    entry = await PipelineCatalogService.add_custom_pipeline(
        session,
        org_id,
        user_id,
        name=data.name,
        source_url=data.source_url,
        version=data.version,
        description=data.description,
    )
    await session.commit()
    return _catalog_response(entry)


@router.patch("/version/{key:path}", response_model=PipelineCatalogResponse)
async def update_pipeline_version(
    key: str,
    data: PipelineVersionUpdateRequest,
    current_user: dict = require_permission("pipelines", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    pipeline = await PipelineCatalogService.get_pipeline(session, org_id, key)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    updated = await PipelineCatalogService.update_pipeline_version(
        session,
        pipeline.id,
        user_id,
        data.version,
    )
    await session.commit()
    return _catalog_response(updated)
