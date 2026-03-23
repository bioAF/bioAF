import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.models.pipeline_run_review import PipelineRunReview
from app.schemas.pipeline_run import (
    ExperimentSummary,
    PipelineProgress,
    PipelineProcessResponse,
    PipelineRunCompareRequest,
    PipelineRunCompareResponse,
    PipelineRunDetailResponse,
    PipelineRunLaunchRequest,
    PipelineRunListResponse,
    PipelineRunResponse,
    SampleSummary,
    UserSummary,
)
from app.services import role_service
from app.services.pipeline_monitor_service import PipelineMonitorService
from app.services.pipeline_run_service import PipelineRunService

router = APIRouter(prefix="/api/pipeline-runs", tags=["pipeline-runs"])


def _run_response(run) -> PipelineRunResponse:
    progress = None
    if run.progress_json:
        progress = PipelineProgress(**run.progress_json)

    return PipelineRunResponse(
        id=run.id,
        pipeline_key=run.pipeline_name,
        pipeline_name=run.pipeline_name,
        pipeline_version=run.pipeline_version,
        experiment=ExperimentSummary(id=run.experiment.id, name=run.experiment.name) if run.experiment else None,
        submitted_by=UserSummary(id=run.submitted_by.id, name=run.submitted_by.name, email=run.submitted_by.email)
        if run.submitted_by
        else None,
        status=run.status,
        parameters=run.parameters_json,
        input_files=run.input_files_json,
        output_files=run.output_files_json,
        progress=progress,
        cost_estimate=float(run.cost_estimate) if run.cost_estimate else None,
        error_message=run.error_message,
        work_dir=run.work_dir,
        slurm_job_id=run.slurm_job_id,
        k8s_job_name=run.k8s_job_name,
        k8s_namespace=run.k8s_namespace,
        k8s_pod_name=run.k8s_pod_name,
        actual_cost=float(run.actual_cost) if run.actual_cost else None,
        reference_genome=run.reference_genome,
        alignment_algorithm=run.alignment_algorithm,
        resume_from_run_id=run.resume_from_run_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _detail_response(run) -> PipelineRunDetailResponse:
    base = _run_response(run)
    processes = [
        PipelineProcessResponse(
            id=p.id,
            process_name=p.process_name,
            task_id=p.task_id,
            status=p.status,
            exit_code=p.exit_code,
            cpu_usage=float(p.cpu_usage) if p.cpu_usage else None,
            memory_peak_gb=float(p.memory_peak_gb) if p.memory_peak_gb else None,
            duration_seconds=p.duration_seconds,
            started_at=p.started_at,
            completed_at=p.completed_at,
        )
        for p in (run.processes or [])
    ]
    samples = [
        SampleSummary(id=s.id, sample_id_external=s.sample_id_external, organism=s.organism)
        for s in (run.samples or [])
    ]
    return PipelineRunDetailResponse(
        **base.model_dump(),
        processes=processes,
        samples=samples,
    )


@router.get("", response_model=PipelineRunListResponse)
async def list_runs(
    page: int = 1,
    page_size: int = 25,
    experiment_id: int | None = None,
    pipeline_key: str | None = None,
    status: str | None = None,
    submitted_by_user_id: int | None = None,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    runs, total = await PipelineRunService.list_runs(
        session,
        org_id,
        page=page,
        page_size=page_size,
        experiment_id=experiment_id,
        pipeline_key=pipeline_key,
        status=status,
        submitted_by_user_id=submitted_by_user_id,
    )
    # Batch-fetch active review verdicts for listed runs
    run_ids = [r.id for r in runs]
    verdict_map: dict[int, str] = {}
    if run_ids:
        review_result = await session.execute(
            select(PipelineRunReview.pipeline_run_id, PipelineRunReview.verdict).where(
                PipelineRunReview.pipeline_run_id.in_(run_ids),
                PipelineRunReview.superseded_by_id.is_(None),
            )
        )
        for row in review_result.all():
            verdict_map[row[0]] = row[1]

    run_responses = []
    for r in runs:
        resp = _run_response(r)
        resp.review_verdict = verdict_map.get(r.id)
        run_responses.append(resp)

    return PipelineRunListResponse(
        runs=run_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=PipelineRunResponse)
async def launch_run(
    data: PipelineRunLaunchRequest,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])

    try:
        run = await PipelineRunService.launch_run(session, org_id, user_id, data)
        await session.commit()

        # Reload with relationships
        run = await PipelineRunService.get_run(session, run.id, org_id)
        return _run_response(run)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{run_id}", response_model=PipelineRunDetailResponse)
async def get_run(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    run = await PipelineRunService.get_run(session, run_id, org_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return _detail_response(run)


@router.post("/{run_id}/cancel", response_model=PipelineRunResponse)
async def cancel_run(
    run_id: int,
    current_user: dict = require_permission("pipelines", "cancel"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])
    run = await PipelineRunService.get_run(session, run_id, org_id)
    if not run:
        raise HTTPException(404, "Run not found")

    can_manage_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    if not can_manage_all and run.submitted_by_user_id != user_id:
        raise HTTPException(403, "Can only cancel your own runs")

    run = await PipelineRunService.cancel_run(session, run_id, user_id)
    await session.commit()

    run = await PipelineRunService.get_run(session, run_id, org_id)
    return _run_response(run)


@router.post("/{run_id}/reproduce", response_model=PipelineRunResponse)
async def reproduce_run(
    run_id: int,
    current_user: dict = require_permission("pipelines", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    try:
        new_run = await PipelineRunService.reproduce_run(session, run_id, user_id)
        await session.commit()
        new_run = await PipelineRunService.get_run(session, new_run.id, org_id)
        return _run_response(new_run)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{run_id}/provenance")
async def get_provenance(
    run_id: int,
    format: str = "json",
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    try:
        provenance = await PipelineRunService.export_provenance(session, run_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if format == "yaml":
        return PlainTextResponse(
            yaml.dump(provenance, default_flow_style=False),
            media_type="text/yaml",
        )
    return provenance


@router.get("/{run_id}/logs")
async def get_run_logs(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Get logs for a K8s pipeline run (no process selection needed)."""
    logs = await PipelineMonitorService.get_run_logs(session, run_id, "")
    return logs


@router.get("/{run_id}/logs/{process_name}")
async def get_process_logs(
    run_id: int,
    process_name: str,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    logs = await PipelineMonitorService.get_run_logs(session, run_id, process_name)
    return logs


@router.get("/{run_id}/report")
async def get_run_report(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    report = await PipelineMonitorService.get_run_report(session, run_id)
    return PlainTextResponse(report, media_type="text/html")


@router.post("/compare", response_model=PipelineRunCompareResponse)
async def compare_runs(
    data: PipelineRunCompareRequest,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    result = await PipelineRunService.compare_runs(session, data.run_ids)
    return PipelineRunCompareResponse(
        runs=[_run_response(r) for r in result["runs"]],
        parameter_diffs=result["parameter_diffs"],
    )


@router.get("/{run_id}/references")
async def get_run_references(
    run_id: int,
    current_user: dict = require_permission("pipelines", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Get reference datasets used by a pipeline run."""
    from app.schemas.reference_dataset import ReferenceDatasetResponse
    from app.services.reference_data_service import ReferenceDataService

    refs = await ReferenceDataService.get_pipeline_run_references(session, run_id)
    return {"references": [ReferenceDatasetResponse.model_validate(r) for r in refs]}
