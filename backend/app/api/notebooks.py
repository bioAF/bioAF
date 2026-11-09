from fastapi import APIRouter, Depends

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.api.notebook_access import can_manage_all_sessions, owned_session_or_404, stop_owned_session
from app.schemas.notebook_session import (
    SessionLaunchRequest,
    SessionResponse,
    SessionListResponse,
    UserSummary,
    ExperimentSummary,
)
from app.services.notebook_service import NotebookService

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _experiment_summary(experiment) -> ExperimentSummary | None:
    if not experiment:
        return None
    return ExperimentSummary(id=experiment.id, name=experiment.name)


def _session_response(ns) -> SessionResponse:
    return SessionResponse(
        id=ns.id,
        session_type=ns.session_type,
        user=_user_summary(ns.user),
        experiment=_experiment_summary(ns.experiment),
        resource_profile=ns.resource_profile,
        cpu_cores=ns.cpu_cores,
        memory_gb=ns.memory_gb,
        status=ns.status,
        idle_since=ns.idle_since,
        proxy_url=ns.proxy_url,
        started_at=ns.started_at,
        stopped_at=ns.stopped_at,
        created_at=ns.created_at,
        git_branch_name=ns.git_branch_name,
        git_commit_hash=ns.git_commit_hash,
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    session_type: str | None = None,
    status: str | None = None,
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    can_view_all = await can_manage_all_sessions(session, current_user)
    filter_user_id = None if can_view_all else user_id

    sessions, total = await NotebookService.list_sessions(
        session,
        org_id,
        user_id=filter_user_id,
        session_type=session_type,
        status=status,
    )
    return SessionListResponse(
        sessions=[_session_response(s) for s in sessions],
        total=total,
    )


@router.post("/sessions", response_model=SessionResponse)
async def launch_session(
    body: SessionLaunchRequest,
    current_user: dict = require_permission("notebooks", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    notebook_session = await NotebookService.launch_session(
        session,
        user_id=user_id,
        org_id=org_id,
        session_type=body.session_type,
        resource_profile=body.resource_profile,
        experiment_id=body.experiment_id,
    )

    await session.commit()

    # Reload with relationships
    notebook_session = await NotebookService.get_session(session, notebook_session.id)
    return _session_response(notebook_session)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_detail(
    session_id: int,
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    notebook_session = await owned_session_or_404(session, session_id, current_user)
    return _session_response(notebook_session)


@router.post("/sessions/{session_id}/stop", response_model=SessionResponse)
async def stop_session(
    session_id: int,
    current_user: dict = require_permission("notebooks", "stop"),
    session: AsyncSession = Depends(get_session),
):
    notebook_session = await stop_owned_session(session, session_id, current_user)
    return _session_response(notebook_session)
