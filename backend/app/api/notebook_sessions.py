"""Phase 22 notebook session API endpoints.

Provides launch, stop, list, detail, sync, and settings endpoints
under /api/v1/notebooks/sessions and /api/v1/settings/.
"""

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
)
from app.services.notebook_service import NotebookService
from app.adapters.registry import get_notebook_adapter

router = APIRouter(prefix="/api/v1/notebooks", tags=["notebook-sessions"])
settings_router = APIRouter(prefix="/api/v1/settings", tags=["notebook-settings"])

logger = __import__("logging").getLogger("bioaf.notebooks.api")


class NotebookLaunchRequest(BaseModel):
    session_type: str
    resource_profile: str = "small"
    experiment_id: int | None = None


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
        proxy_url=ns.access_url or ns.proxy_url,
        started_at=ns.started_at,
        stopped_at=ns.stopped_at,
        created_at=ns.created_at,
    )


async def _get_config_value(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(
        text("SELECT value FROM platform_config WHERE key = :k"),
        {"k": key},
    )
    row = result.first()
    return row[0] if row else None


async def _sync_session_from_k8s(ns, session: AsyncSession) -> None:
    """Check K8s for pod status and LB IP, update session record if needed."""
    if not ns.k8s_pod_name:
        return

    try:
        adapter = get_notebook_adapter()
        if adapter.is_local:
            return

        core_client = adapter._get_k8s_core_client()
        namespace = ns.k8s_namespace or "bioaf-notebooks"
        changed = False

        # Check pod status
        try:
            pod = core_client.read_namespaced_pod(name=ns.k8s_pod_name, namespace=namespace)
            phase = pod.status.phase
            logger.info(
                "Session %s pod %s phase=%s, db_status=%s, access_url=%s",
                ns.id,
                ns.k8s_pod_name,
                phase,
                ns.status,
                ns.access_url,
            )
            if phase == "Running" and ns.status == "starting":
                conditions = pod.status.conditions or []
                ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
                if ready:
                    ns.status = "running"
                    if not ns.started_at:
                        from datetime import datetime, timezone

                        ns.started_at = datetime.now(timezone.utc)
                    changed = True
            elif phase in ("Failed", "Unknown") and ns.status not in ("stopped", "failed"):
                ns.status = "failed"
                changed = True
        except Exception:
            logger.exception("Failed to read pod %s", ns.k8s_pod_name)

        # Check LB IP if we don't have an access_url yet
        if not ns.access_url and ns.status in ("starting", "running"):
            svc_name = f"bioaf-notebook-svc-{ns.id}"
            logger.info(
                "Checking LB IP for session %s, svc=%s, ns=%s",
                ns.id,
                svc_name,
                namespace,
            )
            try:
                # Use raw HTTP to bypass python client caching issues
                import httpx

                api_client = adapter._get_api_client()
                config = api_client.configuration
                url = f"{config.host}/api/v1/namespaces/{namespace}/services/{svc_name}"
                headers = {"Authorization": list(config.api_key.values())[0]}
                resp = httpx.get(
                    url,
                    headers=headers,
                    verify=config.ssl_ca_cert or False,
                    timeout=10,
                )
                logger.info(
                    "Raw K8s API for %s: status=%s",
                    svc_name,
                    resp.status_code,
                )
                if resp.status_code == 200:
                    svc_data = resp.json()
                    ingress_list = svc_data.get("status", {}).get("loadBalancer", {}).get("ingress") or []
                    logger.info("Service %s ingress: %s", svc_name, ingress_list)
                    if ingress_list:
                        ext_ip = ingress_list[0].get("ip") or ingress_list[0].get("hostname")
                        port = 8888 if ns.session_type == "jupyter" else 8787
                        ns.access_url = f"http://{ext_ip}:{port}"
                        changed = True
                        logger.info("Synced LB IP for session %s: %s", ns.id, ns.access_url)
                    else:
                        logger.warning("No ingress for service %s", svc_name)
                else:
                    logger.warning(
                        "K8s API returned %s for %s: %s",
                        resp.status_code,
                        svc_name,
                        resp.text[:200],
                    )
            except Exception:
                logger.exception("Failed to read service %s", svc_name)

        if changed:
            await session.flush()

    except Exception:
        logger.debug("K8s sync failed for session %s", ns.id, exc_info=True)


# -- Session endpoints --


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    session_type: str | None = None,
    status: str | None = None,
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

    try:
        notebook_session = await NotebookService.launch_session(
            session,
            user_id=user_id,
            org_id=org_id,
            session_type=body.session_type,
            resource_profile=body.resource_profile,
            experiment_id=body.experiment_id,
            image=scrna_image,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

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

    try:
        notebook_session = await NotebookService.stop_session(session, session_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

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

    # Attempt GCS sync (best-effort)
    if notebook_session.k8s_pod_name and notebook_session.gcs_home_prefix:
        try:
            from app.services.session_persistence import sync_session_to_gcs

            await sync_session_to_gcs(
                pod_name=notebook_session.k8s_pod_name,
                namespace=notebook_session.k8s_namespace or "bioaf-notebooks",
                gcs_prefix=notebook_session.gcs_home_prefix,
            )
        except Exception:
            pass  # Best effort in local/test mode

    return {"status": "ok", "message": "Sync triggered"}


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
