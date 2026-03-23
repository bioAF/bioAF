"""SSH connection command endpoints for pipeline runs and notebook sessions."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_compute_adapter, get_notebook_adapter
from app.api.dependencies import require_permission
from app.database import get_session
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.schemas.connection import ConnectionCommandResponse
from app.services.audit_service import log_action
from fastapi import Depends

router = APIRouter(tags=["ssh-connect"])

SETUP_GUIDE = """First-time setup for kubectl access:

1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
2. Authenticate: gcloud auth login
3. Get cluster credentials:
   gcloud container clusters get-credentials bioaf-cluster --region <region> --project <project-id>
4. Verify access: kubectl get pods -n bioaf-pipelines

For SLURM-based clusters, ensure SSH access is configured with your system administrator."""

WARNING_TEXT = (
    "Actions performed inside this container are NOT tracked by bioAF audit logs. "
    "Changes may create drift from the tracked pipeline state. "
    "Do not modify input data or pipeline configuration files."
)


@router.post(
    "/api/pipeline-runs/{run_id}/connect",
    response_model=ConnectionCommandResponse,
)
async def connect_pipeline_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("notebooks", "launch"),
):
    """Generate connection command for a running pipeline job."""
    result = await session.execute(
        select(PipelineRun).where(
            PipelineRun.id == run_id,
            PipelineRun.organization_id == current_user["org_id"],
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    if run.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot connect to pipeline run in '{run.status}' state, must be 'running'",
        )

    compute_adapter = get_compute_adapter()
    job_id = run.slurm_job_id or f"pipeline-run-{run.id}"
    command = await compute_adapter.get_connection_command(job_id)

    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type="container_session",
        entity_id=run.id,
        action="connection_command_generated",
        details={
            "target_type": "pipeline_job",
            "target_id": job_id,
            "pipeline_name": run.pipeline_name,
        },
    )
    await session.commit()

    return ConnectionCommandResponse(
        command=command,
        setup_guide=SETUP_GUIDE,
        warning=WARNING_TEXT,
        target_type="pipeline_job",
        target_id=job_id,
        namespace="bioaf-pipelines",
    )


@router.post(
    "/api/sessions/{session_id}/connect",
    response_model=ConnectionCommandResponse,
)
async def connect_notebook_session(
    session_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("notebooks", "launch"),
):
    """Generate connection command for an active notebook session."""
    result = await session.execute(
        select(NotebookSession).where(
            NotebookSession.id == session_id,
            NotebookSession.organization_id == current_user["org_id"],
        )
    )
    nb_session = result.scalar_one_or_none()
    if not nb_session:
        raise HTTPException(status_code=404, detail="Notebook session not found")

    if nb_session.status not in ("running", "idle"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot connect to session in '{nb_session.status}' state, must be 'running' or 'idle'",
        )

    notebook_adapter = get_notebook_adapter()
    target_id = f"notebook-{nb_session.id}"
    command = await notebook_adapter.get_connection_command(target_id)

    await log_action(
        session=session,
        user_id=int(current_user["sub"]),
        entity_type="container_session",
        entity_id=nb_session.id,
        action="connection_command_generated",
        details={
            "target_type": "notebook_session",
            "target_id": target_id,
            "session_type": nb_session.session_type,
        },
    )
    await session.commit()

    return ConnectionCommandResponse(
        command=command,
        setup_guide=SETUP_GUIDE,
        warning=WARNING_TEXT,
        target_type="notebook_session",
        target_id=target_id,
        namespace="bioaf-interactive",
    )


@router.get("/api/infrastructure/compute/pods")
async def list_running_pods(
    session: AsyncSession = Depends(get_session),
    current_user: dict = require_permission("infrastructure", "configure"),
):
    """List all running pods with connect capability (admin only)."""
    compute_adapter = get_compute_adapter()
    jobs = await compute_adapter.list_jobs({"status": "running"})

    notebook_adapter = get_notebook_adapter()
    sessions = await notebook_adapter.list_sessions({"status": "running"})

    pods = []
    for job in jobs:
        pods.append(
            {
                "name": job.get("job_id", "unknown"),
                "type": "pipeline_job",
                "status": job.get("status", "unknown"),
                "namespace": job.get("namespace", "bioaf-pipelines"),
            }
        )
    for s in sessions:
        pods.append(
            {
                "name": s.get("session_id", "unknown"),
                "type": "notebook_session",
                "status": s.get("status", "unknown"),
                "namespace": s.get("namespace", "bioaf-interactive"),
            }
        )

    return {"pods": pods, "total": len(pods)}
