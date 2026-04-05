from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.environment import (
    BuildLogsResponse,
    EnvironmentCreateRequest,
    EnvironmentDetailResponse,
    EnvironmentListResponse,
    EnvironmentResponse,
    EnvironmentUpdateRequest,
    EnvironmentVersionSummary,
    UserSummary,
    VersionCreateRequest,
    VersionResponse,
)
from app.services.environment_service import EnvironmentService

router = APIRouter(prefix="/api/v1/environments", tags=["environments"])


def _version_response(version) -> VersionResponse:
    return VersionResponse(
        id=version.id,
        environment_id=version.environment_id,
        version_number=version.version_number,
        build_number=version.build_number,
        status=version.status,
        definition_format=version.definition_format,
        definition_content=version.definition_content,
        build_id=version.build_id,
        image_uri=version.image_uri,
        created_by=UserSummary(
            id=version.created_by.id,
            name=version.created_by.name,
            email=version.created_by.email,
        )
        if version.created_by
        else None,
        created_at=version.created_at,
    )


def _env_response(env) -> EnvironmentResponse:
    versions = env.versions or []
    latest = None
    if versions:
        sorted_versions = sorted(versions, key=lambda v: v.version_number, reverse=True)
        v = sorted_versions[0]
        latest = EnvironmentVersionSummary(
            id=v.id,
            version_number=v.version_number,
            build_number=v.build_number,
            status=v.status,
            definition_format=v.definition_format,
            image_uri=v.image_uri,
            created_at=v.created_at,
        )

    return EnvironmentResponse(
        id=env.id,
        name=env.name,
        description=env.description,
        visibility=env.visibility,
        version_count=len(versions),
        latest_version=latest,
        created_by=UserSummary(
            id=env.created_by.id,
            name=env.created_by.name,
            email=env.created_by.email,
        )
        if env.created_by
        else None,
        created_at=env.created_at,
        updated_at=env.updated_at,
    )


@router.get("", response_model=EnvironmentListResponse)
async def list_environments(
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    envs = await EnvironmentService.list_environments(session, org_id)
    return EnvironmentListResponse(
        environments=[_env_response(e) for e in envs],
        total=len(envs),
    )


@router.post("", response_model=EnvironmentResponse, status_code=201)
async def create_environment(
    data: EnvironmentCreateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        env = await EnvironmentService.create_environment(
            session,
            org_id,
            user_id,
            name=data.name,
            description=data.description,
            visibility=data.visibility,
        )
        await session.commit()
        # Re-fetch to load relationships
        env = await EnvironmentService.get_environment(session, org_id, env.id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return _env_response(env)


@router.get("/{environment_id}", response_model=EnvironmentDetailResponse)
async def get_environment(
    environment_id: int,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    env = await EnvironmentService.get_environment(session, org_id, environment_id)
    if not env:
        raise HTTPException(404, "Environment not found")

    versions = sorted(env.versions or [], key=lambda v: v.version_number, reverse=True)

    return EnvironmentDetailResponse(
        id=env.id,
        name=env.name,
        description=env.description,
        visibility=env.visibility,
        versions=[
            EnvironmentVersionSummary(
                id=v.id,
                version_number=v.version_number,
                build_number=v.build_number,
                status=v.status,
                definition_format=v.definition_format,
                image_uri=v.image_uri,
                created_at=v.created_at,
            )
            for v in versions
        ],
        created_by=UserSummary(
            id=env.created_by.id,
            name=env.created_by.name,
            email=env.created_by.email,
        )
        if env.created_by
        else None,
        created_at=env.created_at,
        updated_at=env.updated_at,
    )


@router.put("/{environment_id}", response_model=EnvironmentResponse)
async def update_environment(
    environment_id: int,
    data: EnvironmentUpdateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])

    try:
        env = await EnvironmentService.update_environment(
            session,
            org_id,
            environment_id,
            name=data.name,
            description=data.description,
            visibility=data.visibility,
        )
        await session.commit()
        env = await EnvironmentService.get_environment(session, org_id, env.id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return _env_response(env)


@router.delete("/{environment_id}", status_code=204)
async def delete_environment(
    environment_id: int,
    current_user: dict = require_permission("environments", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await EnvironmentService.delete_environment(session, org_id, user_id, environment_id)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- Version endpoints ---


@router.post("/{environment_id}/versions", response_model=VersionResponse, status_code=201)
async def create_version(
    environment_id: int,
    data: VersionCreateRequest,
    current_user: dict = require_permission("environments", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        version = await EnvironmentService.create_version(
            session,
            org_id,
            user_id,
            environment_id,
            definition_format=data.definition_format,
            definition_content=data.definition_content,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Re-fetch to load relationships
    version = await EnvironmentService.get_version(session, org_id, environment_id, version.id)
    return _version_response(version)


@router.post("/{environment_id}/versions/{version_id}/build", response_model=VersionResponse)
async def trigger_build(
    environment_id: int,
    version_id: int,
    current_user: dict = require_permission("environments", "build"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    from app.services.environment_build_service import EnvironmentBuildService

    try:
        await EnvironmentBuildService.build_version(session, org_id, user_id, environment_id, version_id)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    version = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
    if not version:
        raise HTTPException(404, "Version not found")

    return _version_response(version)


@router.get("/{environment_id}/versions/{version_id}", response_model=VersionResponse)
async def get_version(
    environment_id: int,
    version_id: int,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    version = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
    if not version:
        raise HTTPException(404, "Version not found")

    return _version_response(version)


@router.get("/{environment_id}/versions/{version_id}/logs", response_model=BuildLogsResponse)
async def get_build_logs(
    environment_id: int,
    version_id: int,
    current_user: dict = require_permission("environments", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    version = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
    if not version:
        raise HTTPException(404, "Version not found")

    from app.services.environment_build_service import EnvironmentBuildService
    from app.services.notebook_image_service import _read_config

    project_id = await _read_config(session, "gcp_project_id")
    logs_url = await EnvironmentBuildService.get_build_logs_url(session, project_id, version.build_id)

    return BuildLogsResponse(
        build_id=version.build_id,
        status=version.status,
        logs_url=logs_url,
    )


@router.post("/{environment_id}/versions/{version_id}/rebuild", response_model=VersionResponse)
async def rebuild_version(
    environment_id: int,
    version_id: int,
    current_user: dict = require_permission("environments", "build"),
    session: AsyncSession = Depends(get_session),
):
    """Create a rebuild of an existing version (v1 -> v1.2, v1.3, etc.)."""
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        rebuild = await EnvironmentService.rebuild_version(session, org_id, user_id, environment_id, version_id)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    rebuild = await EnvironmentService.get_version(session, org_id, environment_id, rebuild.id)
    return _version_response(rebuild)


@router.delete("/{environment_id}/versions/{version_id}", status_code=204)
async def delete_version(
    environment_id: int,
    version_id: int,
    current_user: dict = require_permission("environments", "delete"),
    session: AsyncSession = Depends(get_session),
):
    """Delete a single environment version and its image."""
    org_id = int(current_user["org_id"])
    version = await EnvironmentService.get_version(session, org_id, environment_id, version_id)
    if not version:
        raise HTTPException(404, "Version not found")

    await EnvironmentService.delete_version(session, org_id, environment_id, version_id)
    await session.commit()


@router.get("/template/dockerfile")
async def get_template_dockerfile(
    current_user: dict = require_permission("environments", "view"),
):
    """Return the current bioAF base Dockerfile template."""
    from app.services.notebook_image_service import DOCKERFILE_CONTENT

    return {"definition_content": DOCKERFILE_CONTENT, "definition_format": "dockerfile"}
