from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import require_permission
from app.api.pipeline_runs import _run_response
from app.database import get_session
from app.models.custom_pipeline import CustomPipeline
from app.models.custom_pipeline_version import CustomPipelineVersion
from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.models.pipeline_run import PipelineRun
from app.schemas.custom_pipeline import (
    CustomPipelineCreateRequest,
    CustomPipelineDetailResponse,
    CustomPipelineEnvironmentSummary,
    CustomPipelineLaunchRequest,
    CustomPipelineResponse,
    CustomPipelineRunOverview,
    CustomPipelineUpdateRequest,
    CustomPipelineVariableResponse,
    CustomPipelineVersionCreateRequest,
    CustomPipelineVersionResponse,
)
from app.services.custom_pipeline_service import CustomPipelineService

router = APIRouter(prefix="/api/v1/custom-pipelines", tags=["custom-pipelines"])


def _pipeline_response(pipeline: CustomPipeline) -> CustomPipelineResponse:
    return CustomPipelineResponse.model_validate(pipeline)


def _version_response(version: CustomPipelineVersion) -> CustomPipelineVersionResponse:
    return CustomPipelineVersionResponse(
        id=version.id,
        version_number=version.version_number,
        code_source_type=version.code_source_type,
        github_repo_id=version.github_repo_id,
        code_content=version.code_content,
        entrypoint_command=version.entrypoint_command,
        environment_version_id=version.environment_version_id,
        cpu_request=version.cpu_request,
        memory_request=version.memory_request,
        log_file_path=version.log_file_path,
        version_trigger=version.version_trigger,
        status=version.status,
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
        variables=[CustomPipelineVariableResponse.model_validate(v) for v in (version.variables or [])],
        qc_template=version.qc_template,
        qc_config_json=version.qc_config_json,
    )


def _detail_response(pipeline: CustomPipeline) -> CustomPipelineDetailResponse:
    return CustomPipelineDetailResponse(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        pipeline_key=pipeline.pipeline_key,
        created_by_user_id=pipeline.created_by_user_id,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        versions=[_version_response(v) for v in (pipeline.versions or [])],
    )


@router.get("", response_model=list[CustomPipelineResponse])
async def list_custom_pipelines(
    current_user: dict = require_permission("custom_pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    pipelines = await CustomPipelineService.list_pipelines(session, org_id)
    return [_pipeline_response(p) for p in pipelines]


@router.post("", response_model=CustomPipelineResponse, status_code=201)
async def create_custom_pipeline(
    data: CustomPipelineCreateRequest,
    current_user: dict = require_permission("custom_pipelines", "create"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        pipeline = await CustomPipelineService.create_pipeline(session, org_id, user_id, data)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    await session.refresh(pipeline)
    return _pipeline_response(pipeline)


@router.get("/{pipeline_id}", response_model=CustomPipelineDetailResponse)
async def get_custom_pipeline(
    pipeline_id: int,
    current_user: dict = require_permission("custom_pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    pipeline = await CustomPipelineService.get_pipeline(session, org_id, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "Custom pipeline not found")
    return _detail_response(pipeline)


@router.put("/{pipeline_id}", response_model=CustomPipelineResponse)
async def update_custom_pipeline(
    pipeline_id: int,
    data: CustomPipelineUpdateRequest,
    current_user: dict = require_permission("custom_pipelines", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        pipeline = await CustomPipelineService.update_pipeline(session, org_id, user_id, pipeline_id, data)
        await session.commit()
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    await session.refresh(pipeline)
    return _pipeline_response(pipeline)


@router.delete("/{pipeline_id}")
async def delete_custom_pipeline(
    pipeline_id: int,
    current_user: dict = require_permission("custom_pipelines", "delete"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await CustomPipelineService.delete_pipeline(session, org_id, user_id, pipeline_id)
        await session.commit()
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    return {"status": "deleted"}


@router.post("/{pipeline_id}/versions", response_model=CustomPipelineVersionResponse, status_code=201)
async def create_custom_pipeline_version(
    pipeline_id: int,
    data: CustomPipelineVersionCreateRequest,
    current_user: dict = require_permission("custom_pipelines", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        version = await CustomPipelineService.create_version(session, org_id, user_id, pipeline_id, data)
        await session.commit()
    except ValueError as e:
        message = str(e)
        if "pipeline not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    return _version_response(version)


@router.get(
    "/{pipeline_id}/versions/{version_id}",
    response_model=CustomPipelineVersionResponse,
)
async def get_custom_pipeline_version(
    pipeline_id: int,
    version_id: int,
    current_user: dict = require_permission("custom_pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    pipeline = await CustomPipelineService.get_pipeline(session, org_id, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "Custom pipeline not found")

    result = await session.execute(
        select(CustomPipelineVersion)
        .where(
            CustomPipelineVersion.id == version_id,
            CustomPipelineVersion.custom_pipeline_id == pipeline_id,
        )
        .options(selectinload(CustomPipelineVersion.variables))
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(404, "Custom pipeline version not found")

    return _version_response(version)


@router.post("/{pipeline_id}/versions/{version_id}/deprecate")
async def deprecate_custom_pipeline_version(
    pipeline_id: int,
    version_id: int,
    current_user: dict = require_permission("custom_pipelines", "edit"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        await CustomPipelineService.deprecate_version(session, org_id, user_id, pipeline_id, version_id)
        await session.commit()
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise HTTPException(404, message)
        raise HTTPException(400, message)

    return {"status": "deprecated"}


@router.get(
    "/versions/{version_id}/overview",
    response_model=CustomPipelineRunOverview,
)
async def get_custom_pipeline_version_overview(
    version_id: int,
    current_user: dict = require_permission("custom_pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Return version + parent pipeline + environment summary in one call.

    Used by the pipeline run detail page so it can render custom pipeline
    metadata without chaining multiple lookups.
    """
    org_id = int(current_user["org_id"])

    result = await session.execute(
        select(CustomPipelineVersion)
        .join(CustomPipeline, CustomPipelineVersion.custom_pipeline_id == CustomPipeline.id)
        .where(
            CustomPipelineVersion.id == version_id,
            CustomPipeline.organization_id == org_id,
        )
        .options(
            selectinload(CustomPipelineVersion.variables),
            selectinload(CustomPipelineVersion.custom_pipeline),
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise HTTPException(404, "Custom pipeline version not found")

    env_result = await session.execute(
        select(EnvironmentVersion, Environment)
        .join(Environment, EnvironmentVersion.environment_id == Environment.id)
        .where(EnvironmentVersion.id == version.environment_version_id)
    )
    env_row = env_result.first()
    env_summary: CustomPipelineEnvironmentSummary | None = None
    if env_row is not None:
        env_version, env = env_row
        env_summary = CustomPipelineEnvironmentSummary(
            environment_id=env.id,
            environment_name=env.name,
            version_id=env_version.id,
            version_number=env_version.version_number,
            build_number=env_version.build_number,
            image_uri=env_version.image_uri,
        )

    pipeline = version.custom_pipeline
    return CustomPipelineRunOverview(
        pipeline_id=pipeline.id,
        pipeline_name=pipeline.name,
        pipeline_key=pipeline.pipeline_key,
        version_id=version.id,
        version_number=version.version_number,
        code_source_type=version.code_source_type,
        github_repo_id=version.github_repo_id,
        entrypoint_command=version.entrypoint_command,
        cpu_request=version.cpu_request,
        memory_request=version.memory_request,
        log_file_path=version.log_file_path,
        environment=env_summary,
        variables=[CustomPipelineVariableResponse.model_validate(v) for v in (version.variables or [])],
    )


@router.post("/{pipeline_id}/launch")
async def launch_custom_pipeline(
    pipeline_id: int,
    data: CustomPipelineLaunchRequest,
    current_user: dict = require_permission("custom_pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    pipeline = await CustomPipelineService.get_pipeline(session, org_id, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "Custom pipeline not found")

    try:
        run = await CustomPipelineService.launch_run(session, org_id, user_id, data)
        await session.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    result = await session.execute(
        select(PipelineRun)
        .where(PipelineRun.id == run.id)
        .options(
            selectinload(PipelineRun.experiment),
            selectinload(PipelineRun.submitted_by),
        )
    )
    loaded = result.scalar_one()
    return _run_response(loaded)
