"""Work node API endpoints (ADR-034).

Provides launch, stop, list, detail, heartbeat, and machine type endpoints
under /api/v1/work-nodes/.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.services import role_service
from app.services.work_node_service import WorkNodeService
from app.services.machine_types import MACHINE_TYPES
from app.schemas.work_node import (
    WorkNodeLaunchRequest,
    WorkNodeResponse,
    WorkNodeListResponse,
    MachineTypeResponse,
    UserSummary,
    WorkNodeSettings,
)
from app.services.audit_service import log_action

router = APIRouter(prefix="/api/v1/work-nodes", tags=["work-nodes"])
settings_router = APIRouter(prefix="/api/v1/settings", tags=["work-node-settings"])

logger = __import__("logging").getLogger("bioaf.work_nodes.api")


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _work_node_response(cs) -> WorkNodeResponse:
    return WorkNodeResponse(
        id=cs.id,
        session_type=cs.session_type,
        user=_user_summary(cs.user) if hasattr(cs, "user") and cs.user else None,
        project_id=cs.project_id,
        environment_version_id=cs.environment_version_id,
        machine_type=cs.machine_type,
        input_file_ids=[v for v in (cs.data_mount_paths or []) if isinstance(v, int)],
        resource_profile=cs.resource_profile,
        cpu_cores=cs.cpu_cores,
        memory_gb=cs.memory_gb,
        status=cs.status,
        access_url=cs.access_url,
        gce_instance_name=cs.gce_instance_name,
        gce_zone=cs.gce_zone,
        github_repo_ids=cs.github_repo_ids,
        heartbeat_at=cs.heartbeat_at,
        started_at=cs.started_at,
        stopped_at=cs.stopped_at,
        created_at=cs.created_at,
    )


async def _get_config_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        text("SELECT value FROM platform_config WHERE key = :k"),
        {"k": key},
    )
    row = result.first()
    return row[0] if row else None


# -- Machine types --


@router.get("/machine-types", response_model=list[MachineTypeResponse])
async def list_machine_types(
    current_user: dict = require_permission("work_nodes", "view"),
):
    return [
        MachineTypeResponse(
            name=mt["name"],
            category=mt["category"],
            cpu=mt["cpu"],
            memory_gb=mt["memory_gb"],
            gpu=mt.get("gpu"),
            description=mt["description"],
        )
        for mt in MACHINE_TYPES
    ]


# -- Sessions --


@router.get("/sessions", response_model=WorkNodeListResponse)
async def list_work_nodes(
    status: str | None = None,
    current_user: dict = require_permission("work_nodes", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    can_view_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    filter_user_id = None if can_view_all else user_id

    sessions_list, total = await WorkNodeService.list_work_nodes(session, org_id, user_id=filter_user_id, status=status)

    return WorkNodeListResponse(
        sessions=[_work_node_response(s) for s in sessions_list],
        total=total,
    )


@router.post("/sessions", response_model=WorkNodeResponse)
async def launch_work_node(
    body: WorkNodeLaunchRequest,
    current_user: dict = require_permission("work_nodes", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    # Check compute is deployed
    compute_deployed = await _get_config_value(session, "compute_deployed")
    if compute_deployed != "true":
        raise HTTPException(
            400,
            "Compute infrastructure is not deployed. Deploy it from Infrastructure > Components first.",
        )

    try:
        compute_session = await WorkNodeService.launch_work_node(
            session,
            user_id=user_id,
            org_id=org_id,
            project_id=body.project_id,
            environment_version_id=body.environment_version_id,
            machine_type=body.machine_type,
            input_file_ids=body.input_file_ids,
            github_repo_ids=body.github_repo_ids,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    await session.commit()

    compute_session = await WorkNodeService.get_work_node(session, compute_session.id)
    return _work_node_response(compute_session)


@router.get("/sessions/{session_id}", response_model=WorkNodeResponse)
async def get_work_node_detail(
    session_id: int,
    current_user: dict = require_permission("work_nodes", "view"),
    session: AsyncSession = Depends(get_session),
):
    compute_session = await WorkNodeService.get_work_node(session, session_id)
    if not compute_session:
        raise HTTPException(404, "Work node not found")
    return _work_node_response(compute_session)


@router.post("/sessions/{session_id}/stop", response_model=WorkNodeResponse)
async def stop_work_node(
    session_id: int,
    current_user: dict = require_permission("work_nodes", "stop"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])

    compute_session = await WorkNodeService.get_work_node(session, session_id)
    if not compute_session:
        raise HTTPException(404, "Work node not found")

    can_manage_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    if not can_manage_all and compute_session.user_id != user_id:
        raise HTTPException(403, "Can only stop your own work nodes")

    try:
        compute_session = await WorkNodeService.stop_work_node(session, session_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await session.commit()
    compute_session = await WorkNodeService.get_work_node(session, compute_session.id)
    return _work_node_response(compute_session)


@router.post("/sessions/{session_id}/heartbeat")
async def record_heartbeat(
    session_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Heartbeat endpoint called by the bioaf CLI inside the Pod.

    Authenticated via X-Heartbeat-Token header (not user JWT).
    """
    token = request.headers.get("X-Heartbeat-Token", "")
    if not token:
        raise HTTPException(403, "Missing heartbeat token")

    valid = await WorkNodeService.record_heartbeat(session, session_id, token)
    if not valid:
        raise HTTPException(403, "Invalid heartbeat token")

    await session.commit()
    return {"status": "ok"}


@router.get("/data-mounts/{project_id}")
async def list_data_mounts(
    project_id: int,
    current_user: dict = require_permission("work_nodes", "view"),
    session: AsyncSession = Depends(get_session),
):
    """List mountable data directories for a project.

    Returns GCS paths for pipeline outputs, uploads, and shared results.
    """
    org_id = int(current_user["org_id"])

    # Verify project exists in org
    from app.models.project import Project
    from sqlalchemy import select

    result = await session.execute(select(Project).where(Project.id == project_id, Project.organization_id == org_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")

    # Build data mount options from project context
    mounts = [
        {
            "path": f"/pipeline-outputs/{project.id}",
            "label": "Pipeline outputs",
            "description": "Results from completed pipeline runs",
        },
        {
            "path": f"/uploads/{project.id}",
            "label": "Uploaded files",
            "description": "Files uploaded to this project",
        },
        {
            "path": f"/shared-results/{project.id}",
            "label": "Shared results",
            "description": "Published analysis results",
        },
    ]

    return mounts


# -- Settings endpoints --

_SETTINGS_KEYS = {
    "max_nodes_per_user": "work_node_max_per_user",
    "idle_timeout_hours": "work_node_idle_timeout_hours",
}

_SETTINGS_DEFAULTS = {
    "max_nodes_per_user": 2,
    "idle_timeout_hours": 24,
}


@settings_router.get("/work-nodes")
async def get_work_node_settings(
    current_user: dict = require_permission("work_nodes", "view"),
    session: AsyncSession = Depends(get_session),
):
    db_keys = list(_SETTINGS_KEYS.values())
    result = await session.execute(
        text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)"),
        {"keys": db_keys},
    )
    rows = {r[0]: r[1] for r in result.fetchall()}

    return {field: int(rows.get(db_key, _SETTINGS_DEFAULTS[field])) for field, db_key in _SETTINGS_KEYS.items()}


@settings_router.put("/work-nodes")
async def update_work_node_settings(
    body: WorkNodeSettings,
    current_user: dict = require_permission("work_nodes", "configure"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    updates = {
        "work_node_max_per_user": str(body.max_nodes_per_user),
        "work_node_idle_timeout_hours": str(body.idle_timeout_hours),
    }

    for key, value in updates.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )

    await log_action(
        session,
        user_id=user_id,
        entity_type="platform_config",
        entity_id=0,
        action="update_work_node_settings",
        details=updates,
    )
    await session.commit()
    return {"status": "ok"}
