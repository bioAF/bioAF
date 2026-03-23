from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.schemas.compute import ClusterStatusResponse, PartitionStatus, BudgetResponse
from app.schemas.slurm_job import JobResponse, JobListResponse
from app.schemas.notebook_session import UserSummary, ExperimentSummary
from app.adapters.registry import get_compute_adapter
from app.services import role_service
from app.services.slurm_service import SlurmService
from app.services.compute_cost_service import ComputeCostService

router = APIRouter(prefix="/api/compute", tags=["compute"])


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _experiment_summary(experiment) -> ExperimentSummary | None:
    if not experiment:
        return None
    return ExperimentSummary(id=experiment.id, name=experiment.name)


def _job_response(job) -> JobResponse:
    return JobResponse(
        id=job.id,
        slurm_job_id=job.slurm_job_id,
        job_name=job.job_name,
        partition=job.partition,
        status=job.status,
        user=_user_summary(job.user),
        experiment=_experiment_summary(job.experiment),
        cpu_requested=job.cpu_requested,
        memory_gb_requested=job.memory_gb_requested,
        cpu_used=job.cpu_used,
        memory_gb_used=job.memory_gb_used,
        exit_code=job.exit_code,
        cost_estimate=float(job.cost_estimate) if job.cost_estimate else None,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/cluster", response_model=ClusterStatusResponse)
async def get_cluster_status(
    current_user: dict = require_permission("infrastructure", "change_status"),
):
    # Use the BAL compute adapter for cluster status
    try:
        compute_adapter = get_compute_adapter()
        status = await compute_adapter.get_cluster_status()

        # Normalize node_pools to partitions for backward compatibility
        node_pools = status.get("node_pools", status.get("partitions", []))
        partitions = []
        for pool in node_pools:
            partitions.append(
                PartitionStatus(
                    name=pool.get("name", "unknown"),
                    max_nodes=pool.get("max_nodes", pool.get("current_nodes", 0)),
                    active_nodes=pool.get("active_nodes", pool.get("current_nodes", 0)),
                    idle_nodes=pool.get("idle_nodes", 0),
                    queue_depth=pool.get("queue_depth", 0),
                    instance_type=pool.get("instance_type", pool.get("machine_type", "unknown")),
                    use_spot=pool.get("use_spot", pool.get("spot", False)),
                )
            )

        return ClusterStatusResponse(
            controller_status=status.get("controller_status", "unknown"),
            partitions=partitions,
            total_nodes=status.get("total_nodes", 0),
            active_nodes=status.get("active_nodes", 0),
            queue_depth=status.get("queue_depth", 0),
            cost_burn_rate_hourly=status.get("cost_burn_rate_hourly"),
        )
    except RuntimeError:
        # Adapter not initialized, fall back to legacy SLURM service
        status = await SlurmService.get_cluster_status()
        return ClusterStatusResponse(
            controller_status=status["controller_status"],
            partitions=[PartitionStatus(**p) for p in status["partitions"]],
            total_nodes=status["total_nodes"],
            active_nodes=status["active_nodes"],
            queue_depth=status["queue_depth"],
            cost_burn_rate_hourly=status.get("cost_burn_rate_hourly"),
        )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    page: int = 1,
    page_size: int = 25,
    user_id: int | None = None,
    status: str | None = None,
    partition: str | None = None,
    experiment_id: int | None = None,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    jobs, total = await SlurmService.list_jobs(
        session,
        org_id,
        page=page,
        page_size=page_size,
        user_id=user_id,
        status=status,
        partition=partition,
        experiment_id=experiment_id,
    )
    return JobListResponse(
        jobs=[_job_response(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
):
    job = await SlurmService.get_job(session, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: int,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    job = await SlurmService.get_job(session, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    can_manage_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    if not can_manage_all and job.user_id != user_id:
        raise HTTPException(403, "Can only cancel your own jobs")

    job = await SlurmService.cancel_job(session, job_id, user_id)
    await session.commit()
    return _job_response(job)


@router.post("/jobs/{job_id}/resubmit", response_model=JobResponse)
async def resubmit_job(
    job_id: int,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    new_job = await SlurmService.resubmit_job(session, job_id, user_id, org_id)
    await session.commit()
    return _job_response(new_job)


@router.get("/budget", response_model=BudgetResponse)
async def get_budget(
    current_user: dict = require_permission("infrastructure", "view"),
):
    # Use the BAL compute adapter for metrics
    try:
        compute_adapter = get_compute_adapter()
        metrics = await compute_adapter.get_cluster_metrics()
        burn_rate = metrics.get("cost_burn_rate_hourly", 0.0) or 0.0
    except RuntimeError:
        status = await SlurmService.get_cluster_status()
        burn_rate = status.get("cost_burn_rate_hourly", 0.0) or 0.0

    budget = ComputeCostService.get_monthly_spend_estimate(burn_rate)
    return BudgetResponse(**budget)
