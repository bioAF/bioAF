from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.pipeline import (
    PipelineAddRequest,
    PipelineCatalogListResponse,
    PipelineCatalogResponse,
    PipelineVersionUpdateRequest,
    RegistryInstallRequest,
    RegistryListResponse,
    RegistryPipelineItem,
    RegistryRefreshResponse,
    RegistryVersion,
    RegistryVersionsResponse,
)
from app.services.nf_core_registry_service import NfCoreRegistryService
from app.services.pipeline_catalog_service import PipelineCatalogService

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _catalog_response(
    entry,
    created_by_username: str | None = None,
    latest_version_number: int | None = None,
) -> PipelineCatalogResponse:
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
        custom_pipeline_id=entry.custom_pipeline_id,
        created_by_username=created_by_username,
        latest_version_number=latest_version_number,
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

    enriched = await PipelineCatalogService.list_pipelines(session, org_id)
    return PipelineCatalogListResponse(
        pipelines=[_catalog_response(entry, username, latest) for entry, username, latest in enriched],
        total=len(enriched),
    )


# ---- nf-core registry routes ----
# IMPORTANT: these MUST be declared before GET /{key:path} or FastAPI's
# path-converter will swallow /registry, /registry/{name}/..., and /registry/refresh.


@router.get("/registry", response_model=RegistryListResponse)
async def list_registry_pipelines(
    q: str | None = None,
    only_installed: bool = False,
    include_archived: bool = False,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    rows = await NfCoreRegistryService.list_pipelines_with_status(
        session,
        org_id,
        q=q,
        only_installed=only_installed,
        include_archived=include_archived,
    )
    last_refreshed_at = await NfCoreRegistryService.get_last_refreshed_at(session)
    return RegistryListResponse(
        pipelines=[RegistryPipelineItem(**r) for r in rows],
        total=len(rows),
        last_refreshed_at=last_refreshed_at,
    )


@router.get("/registry/{name}/versions", response_model=RegistryVersionsResponse)
async def get_registry_pipeline_versions(
    name: str,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    versions = await NfCoreRegistryService.get_pipeline_versions(session, name)
    return RegistryVersionsResponse(
        name=name,
        versions=[RegistryVersion(**v) for v in versions],
    )


@router.post("/registry/{name}/install", response_model=PipelineCatalogResponse)
async def install_registry_pipeline(
    name: str,
    data: RegistryInstallRequest,
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    try:
        entry = await NfCoreRegistryService.install_pipeline(session, org_id, user_id, name, data.version)
    except NfCoreRegistryService.PipelineNotInRegistryError:
        raise HTTPException(404, f"Pipeline '{name}' not found in nf-core registry")
    except NfCoreRegistryService.PipelineAlreadyInstalledError:
        raise HTTPException(409, f"Pipeline 'nf-core/{name}' is already installed")
    await session.commit()
    return _catalog_response(entry)


@router.post("/registry/refresh", response_model=RegistryRefreshResponse)
async def refresh_registry_endpoint(
    current_user: dict = require_permission("pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    result = await NfCoreRegistryService.refresh_registry(session)
    last_refreshed_at = await NfCoreRegistryService.get_last_refreshed_at(session)
    # Audit only on manual refresh; the background loop stays silent.
    from app.services.audit_service import log_action

    await log_action(
        session,
        user_id=user_id,
        entity_type="nf_core_registry",
        entity_id=1,
        action="manual_refresh",
        details={"fetched": result["fetched"], "archived": result["archived"], "error": result.get("error")},
    )
    await session.commit()
    return RegistryRefreshResponse(
        fetched=result["fetched"],
        archived=result["archived"],
        error=result.get("error"),
        last_refreshed_at=last_refreshed_at,
    )


# ---- end nf-core registry routes ----


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
