from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.schemas.experiment_auto_run import (
    AutoRunConfigCreate,
    AutoRunConfigResponse,
    AutoRunConfigUpdate,
    PendingAutoRunResponse,
)
from app.services.auto_run_service import AutoRunService
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/experiments", tags=["experiment_auto_runs"])


def _config_response(config) -> AutoRunConfigResponse:
    return AutoRunConfigResponse(
        id=config.id,
        experiment_id=config.experiment_id,
        pipeline_key=config.pipeline_key,
        parameters=config.parameters_json,
        reference_genome=config.reference_genome,
        alignment_algorithm=config.alignment_algorithm,
        delay_minutes=config.delay_minutes,
        enabled=config.enabled,
        configured_by_user_id=config.configured_by_user_id,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.post("/{experiment_id}/auto-runs", response_model=AutoRunConfigResponse)
async def create_auto_run_config(
    experiment_id: int,
    body: AutoRunConfigCreate,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])
    try:
        config = await AutoRunService.create_config(
            session,
            experiment_id=experiment_id,
            org_id=org_id,
            user_id=user_id,
            pipeline_key=body.pipeline_key,
            parameters=body.parameters,
            reference_genome=body.reference_genome,
            alignment_algorithm=body.alignment_algorithm,
            delay_minutes=body.delay_minutes,
        )
        await log_action(
            session,
            user_id=user_id,
            entity_type="experiment_auto_run",
            entity_id=config.id,
            action="create",
            details={
                "experiment_id": experiment_id,
                "pipeline_key": body.pipeline_key,
                "delay_minutes": body.delay_minutes,
            },
        )
        await session.commit()
        return _config_response(config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{experiment_id}/auto-runs", response_model=list[AutoRunConfigResponse])
async def list_auto_run_configs(
    experiment_id: int,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    configs = await AutoRunService.list_configs(session, experiment_id, org_id)
    return [_config_response(c) for c in configs]


@router.patch("/{experiment_id}/auto-runs/{config_id}", response_model=AutoRunConfigResponse)
async def update_auto_run_config(
    experiment_id: int,
    config_id: int,
    body: AutoRunConfigUpdate,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    updates = body.model_dump(exclude_unset=True)
    config = await AutoRunService.update_config(session, config_id, org_id, updates)
    if not config:
        raise HTTPException(404, "Auto-run configuration not found")

    user_id = int(current_user["sub"])
    await log_action(
        session,
        user_id=user_id,
        entity_type="experiment_auto_run",
        entity_id=config_id,
        action="update",
        details=updates,
    )
    await session.commit()

    # Re-fetch after commit so onupdate fields are current
    from sqlalchemy import select
    from app.models.experiment_auto_run import ExperimentAutoRun

    result = await session.execute(select(ExperimentAutoRun).where(ExperimentAutoRun.id == config_id))
    config = result.scalar_one()
    return _config_response(config)


@router.delete("/{experiment_id}/auto-runs/{config_id}")
async def delete_auto_run_config(
    experiment_id: int,
    config_id: int,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    deleted = await AutoRunService.delete_config(session, config_id, org_id)
    if not deleted:
        raise HTTPException(404, "Auto-run configuration not found")

    user_id = int(current_user["sub"])
    await log_action(
        session,
        user_id=user_id,
        entity_type="experiment_auto_run",
        entity_id=config_id,
        action="delete",
        details={"experiment_id": experiment_id},
    )
    await session.commit()
    return {"status": "deleted"}


@router.get(
    "/{experiment_id}/auto-runs/{config_id}/pending",
    response_model=list[PendingAutoRunResponse],
)
async def list_pending_auto_runs(
    experiment_id: int,
    config_id: int,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    runs = await AutoRunService.list_pending_runs(session, config_id, org_id)
    return [
        PendingAutoRunResponse(
            id=r.id,
            auto_run_config_id=r.auto_run_config_id,
            experiment_id=r.experiment_id,
            sample_id=r.sample_id,
            sample_completed_at=r.sample_completed_at,
            scheduled_at=r.scheduled_at,
            status=r.status,
            pipeline_run_id=r.pipeline_run_id,
            cancelled_reason=r.cancelled_reason,
            created_at=r.created_at,
        )
        for r in runs
    ]
