"""Phase 22 notebook session API endpoints.

Provides launch, stop, list, detail, sync, and settings endpoints
under /api/v1/notebooks/sessions and /api/v1/settings/.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.api.dependencies import require_permission
from app.services import role_service
from app.schemas.notebook_session import (
    SessionResponse,
    SessionListResponse,
    UserSummary,
    ExperimentSummary,
    ProjectSummary,
)
from app.services.notebook_service import NotebookService
from app.adapters.models import ServiceState
from app.adapters.registry import get_notebook_adapter

router = APIRouter(prefix="/api/v1/notebooks", tags=["notebook-sessions"])
settings_router = APIRouter(prefix="/api/v1/settings", tags=["notebook-settings"])

logger = __import__("logging").getLogger("bioaf.notebooks.api")


class NotebookLaunchRequest(BaseModel):
    session_type: str
    resource_profile: Literal["small", "medium", "large", "xlarge", "2xlarge"] = "small"
    experiment_id: int | None = None
    input_file_ids: list[int] = []
    environment_version_id: int | None = None


class NotebookSettings(BaseModel):
    idle_timeout_hours: int = 4
    idle_warning_minutes: int = 15
    max_sessions_per_user: int = 2


class ContainerRegistryConfig(BaseModel):
    bioaf_scrna_image: str


def _user_summary(user) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(id=user.id, name=user.name, email=user.email)


def _experiment_summary(experiment) -> ExperimentSummary | None:
    if not experiment:
        return None
    return ExperimentSummary(id=experiment.id, name=experiment.name)


def _project_summary(project) -> ProjectSummary | None:
    if not project:
        return None
    return ProjectSummary(id=project.id, name=project.name)


def _session_response(ns) -> SessionResponse:
    # accessed_files is the NotebookSessionFile join. Filter for input rows so
    # the frontend's Recreate flow can re-select the same files.
    input_file_ids = None
    accessed = getattr(ns, "accessed_files", None)
    if accessed is not None:
        try:
            input_file_ids = sorted(
                {nsf.file_id for nsf in accessed if getattr(nsf, "access_type", "input") == "input"}
            )
        except Exception:
            input_file_ids = None
    return SessionResponse(
        id=ns.id,
        session_type=ns.session_type,
        user=_user_summary(ns.user),
        experiment=_experiment_summary(ns.experiment),
        project=_project_summary(getattr(ns, "project", None)),
        resource_profile=ns.resource_profile,
        cpu_cores=ns.cpu_cores,
        memory_gb=ns.memory_gb,
        requested_disk_gb=ns.requested_disk_gb,
        status=ns.status,
        failure_reason=ns.failure_reason,
        failure_message=ns.failure_message,
        idle_since=ns.idle_since,
        proxy_url=ns.access_url or ns.proxy_url,
        started_at=ns.started_at,
        stopped_at=ns.stopped_at,
        created_at=ns.created_at,
        git_branch_name=ns.git_branch_name,
        git_commit_hash=ns.git_commit_hash,
        environment_version_id=ns.environment_version_id,
        input_file_ids=input_file_ids,
        provider_metadata=ns.provider_metadata,
    )


async def _get_config_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        text("SELECT value FROM platform_config WHERE key = :k"),
        {"k": key},
    )
    row = result.first()
    return row[0] if row else None


async def _sync_session_from_k8s(ns, session: AsyncSession) -> None:
    """Reconcile a session's status and access_url from the notebook adapter.

    Reads the normalized SessionStatus from the NotebookProvider interface
    (which resolves pod phase and the LoadBalancer URL) and maps it back onto
    the DB record. No backend privates or K8s service URLs leak in here.
    """
    if not ns.compute_job_ref:
        return

    try:
        adapter = get_notebook_adapter()
        status = await adapter.get_session_status(
            session_id=ns.id,
            pod_name=ns.compute_job_ref,
            namespace=ns.provider_namespace or "bioaf-notebooks",
            session_type=ns.session_type,
        )
    except Exception:
        logger.debug("Adapter session status failed for session %s", ns.id, exc_info=True)
        return

    changed = False

    if status.status == ServiceState.RUNNING and ns.status == "starting":
        ns.status = "running"
        if not ns.started_at:
            from datetime import datetime, timezone

            ns.started_at = datetime.now(timezone.utc)
        changed = True
    elif status.status == ServiceState.ERROR and ns.status not in ("stopped", "failed"):
        ns.status = "failed"
        changed = True

    if not ns.access_url and status.access_url and ns.status in ("starting", "running"):
        ns.access_url = status.access_url
        changed = True

    if changed:
        await session.flush()


# -- Session endpoints --


@router.get("/resource-profiles")
async def list_resource_profiles(
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Resource profile ladder with availability for the configured interactive pool."""
    return await NotebookService.get_resource_profile_availability(session)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    session_type: str | None = None,
    status: str | None = None,
    bucket: Literal["active", "recent", "all"] | None = None,
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    org_id = int(current_user["org_id"])
    user_id = int(current_user["sub"])
    can_view_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    filter_user_id = None if can_view_all else user_id

    sessions_list, total = await NotebookService.list_sessions(
        session,
        org_id,
        user_id=filter_user_id,
        session_type=session_type,
        status=status,
        bucket=bucket,
    )

    # Sync active sessions that are missing access_url or still starting
    for s in sessions_list:
        if s.status in ("starting", "running") and (not s.access_url or s.status == "starting"):
            await _sync_session_from_k8s(s, session)

    await session.commit()

    return SessionListResponse(
        sessions=[_session_response(s) for s in sessions_list],
        total=total,
    )


@router.post("/sessions", response_model=SessionResponse)
async def launch_session(
    body: NotebookLaunchRequest,
    current_user: dict = require_permission("notebooks", "launch"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])
    org_id = int(current_user["org_id"])

    # Check preconditions
    compute_deployed = await _get_config_value(session, "compute_deployed")
    if compute_deployed != "true":
        raise HTTPException(
            400,
            "Compute infrastructure is not deployed. Deploy it from Infrastructure > Components first.",
        )

    # Resolve image: prefer environment_version_id, fall back to global scrna_image
    image: str | None = None
    environment_version_id = body.environment_version_id

    if environment_version_id:
        from app.models.environment_version import EnvironmentVersion
        from sqlalchemy import select as sa_select

        ev_result = await session.execute(
            sa_select(EnvironmentVersion).where(EnvironmentVersion.id == environment_version_id)
        )
        env_version = ev_result.scalar_one_or_none()
        if not env_version:
            raise HTTPException(400, "Environment version not found")
        if env_version.status != "ready":
            raise HTTPException(
                400,
                f"Environment version must be in ready status (current: {env_version.status}). "
                "Build the environment first.",
            )
        image = env_version.image_uri
    else:
        scrna_image = await _get_config_value(session, "bioaf_scrna_image")
        if not scrna_image or scrna_image == "null":
            build_status = await _get_config_value(session, "notebook_image_build_status")
            if build_status in ("QUEUED", "WORKING"):
                raise HTTPException(
                    400,
                    "The notebook image is currently building. "
                    "This one-time setup can take up to an hour. Check progress in Infrastructure > Components.",
                )
            raise HTTPException(
                400,
                "The notebook image has not been built yet. "
                "Enable RStudio or JupyterHub in Infrastructure > Components to start the build.",
            )
        image = scrna_image

    notebook_session = await NotebookService.launch_session(
        session,
        user_id=user_id,
        org_id=org_id,
        session_type=body.session_type,
        resource_profile=body.resource_profile,
        experiment_id=body.experiment_id,
        image=image,
        input_file_ids=body.input_file_ids or None,
        environment_version_id=environment_version_id,
    )

    await session.commit()

    notebook_session = await NotebookService.get_session(session, notebook_session.id)
    return _session_response(notebook_session)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_detail(
    session_id: int,
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    notebook_session = await NotebookService.get_session(session, session_id)
    if not notebook_session:
        raise HTTPException(404, "Session not found")

    from app.services.audit_service import log_action

    user_id = int(current_user["sub"])
    await log_action(
        session,
        user_id=user_id,
        entity_type="notebook",
        entity_id=notebook_session.id,
        action="session_access",
        details={
            "session_type": notebook_session.session_type,
            "status": notebook_session.status,
        },
    )
    await session.commit()

    return _session_response(notebook_session)


@router.post("/sessions/{session_id}/stop", response_model=SessionResponse)
async def stop_session(
    session_id: int,
    current_user: dict = require_permission("notebooks", "stop"),
    session: AsyncSession = Depends(get_session),
):
    user_id = int(current_user["sub"])

    notebook_session = await NotebookService.get_session(session, session_id)
    if not notebook_session:
        raise HTTPException(404, "Session not found")

    can_manage_all = await role_service.has_permission(session, int(current_user["role_id"]), "users", "deactivate")
    if not can_manage_all and notebook_session.user_id != user_id:
        raise HTTPException(403, "Can only stop your own sessions")

    notebook_session = await NotebookService.stop_session(session, session_id, user_id)

    await session.commit()
    notebook_session = await NotebookService.get_session(session, notebook_session.id)
    return _session_response(notebook_session)


@router.post("/sessions/{session_id}/sync")
async def sync_session(
    session_id: int,
    current_user: dict = require_permission("notebooks", "edit"),
    session: AsyncSession = Depends(get_session),
):
    notebook_session = await NotebookService.get_session(session, session_id)
    if not notebook_session:
        raise HTTPException(404, "Session not found")

    if notebook_session.status != "running":
        raise HTTPException(400, "Session is not running")

    # Attempt session storage sync (best-effort), via the notebook adapter.
    if notebook_session.compute_job_ref and notebook_session.gcs_home_prefix:
        try:
            adapter = get_notebook_adapter()
            await adapter.sync_session_storage(
                session_id=notebook_session.id,
                pod_name=notebook_session.compute_job_ref,
                namespace=notebook_session.provider_namespace or "bioaf-notebooks",
                gcs_prefix=notebook_session.gcs_home_prefix,
            )
        except Exception:
            pass  # Best effort in local/test mode

    return {"status": "ok", "message": "Sync triggered"}


@router.get("/sessions/{session_id}/provenance")
async def get_session_provenance(
    session_id: int,
    current_user: dict = require_permission("notebooks", "view"),
    session: AsyncSession = Depends(get_session),
):
    """Return full provenance for a session: inputs, outputs, environment, user."""
    notebook_session = await NotebookService.get_session(session, session_id)
    if not notebook_session:
        raise HTTPException(404, "Session not found")

    from app.models.notebook_session_file import NotebookSessionFile
    from app.models.file import File
    from sqlalchemy import select as sa_select

    # Gather input and output files
    nsf_result = await session.execute(
        sa_select(NotebookSessionFile, File)
        .join(File, File.id == NotebookSessionFile.file_id)
        .where(NotebookSessionFile.session_id == session_id)
    )
    input_files = []
    output_files = []
    for nsf, f in nsf_result.all():
        file_info = {
            "id": f.id,
            "filename": f.filename,
            "gcs_uri": f.gcs_uri,
            "file_type": f.file_type,
            "size_bytes": f.size_bytes,
        }
        if nsf.access_type == "input":
            input_files.append(file_info)
        else:
            output_files.append(file_info)

    # Gather environment version info
    environment = None
    if notebook_session.environment_version_id:
        from app.models.environment_version import EnvironmentVersion
        from app.models.environment import Environment

        ev_result = await session.execute(
            sa_select(EnvironmentVersion, Environment)
            .join(Environment, Environment.id == EnvironmentVersion.environment_id)
            .where(EnvironmentVersion.id == notebook_session.environment_version_id)
        )
        row = ev_result.first()
        if row:
            ev, env = row
            environment = {
                "environment_id": env.id,
                "environment_name": env.name,
                "version_id": ev.id,
                "version_number": ev.version_number,
                "build_number": ev.build_number,
                "image_uri": ev.image_uri,
                "definition_format": ev.definition_format,
            }

    return {
        "session_id": notebook_session.id,
        "session_type": notebook_session.session_type,
        "status": notebook_session.status,
        "user": {
            "id": notebook_session.user.id,
            "name": notebook_session.user.name,
            "email": notebook_session.user.email,
        }
        if notebook_session.user
        else None,
        "project_id": notebook_session.project_id,
        "experiment_id": notebook_session.experiment_id,
        "environment": environment,
        "input_files": input_files,
        "output_files": output_files,
        "gcs_output_prefix": notebook_session.gcs_output_prefix,
        "started_at": notebook_session.started_at.isoformat() if notebook_session.started_at else None,
        "stopped_at": notebook_session.stopped_at.isoformat() if notebook_session.stopped_at else None,
    }


# System files and directories to exclude from output registration
_EXCLUDED_FILENAMES = {
    ".bash_history",
    ".Rhistory",
    ".bash_logout",
    ".bashrc",
    ".profile",
    ".gitconfig",
    ".ssh",
    ".local",
    ".cache",
    ".ipython",
}
_EXCLUDED_PREFIXES = ("data/", ".git/", "__pycache__/", ".ipynb_checkpoints/")


class OutputRegistrationRequest(BaseModel):
    outputs: list[dict]


def _file_type_from_extension(filename: str) -> str:
    """Derive file_type from filename extension."""
    parts = filename.rsplit(".", 1)
    if len(parts) < 2:
        return "unknown"
    ext = parts[1].lower()
    # Handle double extensions
    if filename.lower().endswith(".fastq.gz"):
        return "fastq"
    return ext


@router.post("/sessions/{session_id}/register-outputs")
async def register_outputs(
    session_id: int,
    body: OutputRegistrationRequest,
    current_user: dict = require_permission("notebooks", "edit"),
    session: AsyncSession = Depends(get_session),
):
    notebook_session = await NotebookService.get_session(session, session_id)
    if not notebook_session:
        raise HTTPException(404, "Session not found")

    from app.models.file import File
    from app.models.notebook_session_file import NotebookSessionFile

    registered = 0
    for output in body.outputs:
        filename = output.get("filename", "")
        size_bytes = output.get("size_bytes", 0)
        gcs_uri = output.get("gcs_uri", "")

        # Exclude system files
        if filename in _EXCLUDED_FILENAMES or filename.startswith("."):
            if filename in _EXCLUDED_FILENAMES:
                continue
            # Allow dotfiles that aren't in the exclusion set only if they have a useful extension
            base = filename.lstrip(".")
            if not base or "." not in base:
                continue

        # Exclude /data/ directory files (inputs)
        if any(filename.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue

        file_record = File(
            organization_id=notebook_session.organization_id,
            gcs_uri=gcs_uri,
            filename=filename,
            size_bytes=size_bytes,
            file_type=_file_type_from_extension(filename),
            experiment_id=notebook_session.experiment_id,
            project_id=notebook_session.project_id,
            source_type="notebook_output",
            source_notebook_session_id=session_id,
            uploader_user_id=notebook_session.user_id,
        )
        session.add(file_record)
        await session.flush()

        session.add(
            NotebookSessionFile(
                session_id=session_id,
                file_id=file_record.id,
                access_type="output",
            )
        )
        registered += 1

    await session.commit()
    return {"status": "ok", "registered_count": registered}


# -- Settings endpoints --


@settings_router.get("/notebooks")
async def get_notebook_settings(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    defaults = {
        "idle_timeout_hours": 4,
        "idle_warning_minutes": 15,
        "max_sessions_per_user": 2,
        "bioaf_scrna_image": "",
    }
    db_keys = [
        "notebook_idle_timeout_hours",
        "notebook_idle_warning_minutes",
        "notebook_max_sessions_per_user",
        "bioaf_scrna_image",
    ]
    result = await session.execute(
        text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)"),
        {"keys": db_keys},
    )
    rows = {r[0]: r[1] for r in result.fetchall()}

    return {
        "idle_timeout_hours": int(rows.get("notebook_idle_timeout_hours", defaults["idle_timeout_hours"])),
        "idle_warning_minutes": int(rows.get("notebook_idle_warning_minutes", defaults["idle_warning_minutes"])),
        "max_sessions_per_user": int(rows.get("notebook_max_sessions_per_user", defaults["max_sessions_per_user"])),
        "bioaf_scrna_image": rows.get("bioaf_scrna_image", "") if rows.get("bioaf_scrna_image") != "null" else "",
    }


@settings_router.put("/notebooks")
async def update_notebook_settings(
    body: NotebookSettings,
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
):
    for key, value in [
        ("notebook_idle_timeout_hours", str(body.idle_timeout_hours)),
        ("notebook_idle_warning_minutes", str(body.idle_warning_minutes)),
        ("notebook_max_sessions_per_user", str(body.max_sessions_per_user)),
    ]:
        await session.execute(
            text("""
                INSERT INTO platform_config (key, value) VALUES (:k, :v)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """),
            {"k": key, "v": value},
        )
    await session.commit()
    return {"status": "ok"}


@settings_router.put("/container-registry")
async def update_container_registry(
    body: ContainerRegistryConfig,
    current_user: dict = require_permission("notebooks", "edit"),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        text("""
            INSERT INTO platform_config (key, value) VALUES (:k, :v)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """),
        {"k": "bioaf_scrna_image", "v": body.bioaf_scrna_image},
    )
    await session.commit()
    return {"status": "ok"}
