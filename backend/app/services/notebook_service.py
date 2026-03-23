import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notebook_session import NotebookSession
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import SESSION_IDLE
from app.services.quota_service import QuotaService
from app.adapters.registry import get_notebook_adapter

logger = logging.getLogger("bioaf.notebooks")

RESOURCE_PROFILES = {
    "small": (2, 4),
    "medium": (4, 8),
    "large": (8, 16),
}


class NotebookService:
    @staticmethod
    def get_resource_profile(profile_name: str) -> tuple[int, int]:
        if profile_name not in RESOURCE_PROFILES:
            raise ValueError(f"Invalid resource profile: {profile_name}")
        return RESOURCE_PROFILES[profile_name]

    @staticmethod
    async def launch_session(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        session_type: str,
        resource_profile: str,
        experiment_id: int | None = None,
        project_id: int | None = None,
        image: str | None = None,
    ) -> NotebookSession:
        # Check quota
        allowed, message = await QuotaService.check_quota(session, user_id, estimated_hours=1.0)
        if not allowed:
            raise ValueError(f"Quota exceeded: {message}")

        cpu_cores, memory_gb = NotebookService.get_resource_profile(resource_profile)

        notebook_session = NotebookSession(
            user_id=user_id,
            organization_id=org_id,
            session_type=session_type,
            experiment_id=experiment_id,
            project_id=project_id,
            resource_profile=resource_profile,
            cpu_cores=cpu_cores,
            memory_gb=memory_gb,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        session.add(notebook_session)
        await session.flush()

        # Launch via the notebook adapter (BAL)
        try:
            notebook_adapter = get_notebook_adapter()
            spec: dict = {
                "session_type": session_type,
                "resource_profile": resource_profile,
                "cpu_cores": cpu_cores,
                "memory_gb": memory_gb,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "user_id": user_id,
                "session_id": notebook_session.id,
            }
            if image:
                spec["image"] = image

            # RStudio requires session credentials for PAM auth
            if session_type == "rstudio":
                from app.services.session_credential_service import SessionCredentialService

                cred = await SessionCredentialService.get_by_user_id(session, user_id)
                if not cred:
                    raise ValueError(
                        "Session credentials are required for RStudio sessions. "
                        "Please set up your session credentials in your profile settings."
                    )
                spec["session_credentials"] = {
                    "username": cred.username,
                    "password_hash": cred.password_hash,
                }

            result = await notebook_adapter.launch_session(spec)

            notebook_session.slurm_job_id = str(result.get("session_id", ""))
            notebook_session.proxy_url = result.get("url")
            notebook_session.k8s_pod_name = result.get("pod_name")
            notebook_session.k8s_namespace = result.get("namespace")
            notebook_session.access_url = result.get("access_url")
            notebook_session.gcs_home_prefix = result.get("gcs_home_prefix")
            adapter_status = result.get("status", "starting")
            if adapter_status == "error":
                notebook_session.status = "failed"
            elif adapter_status == "running":
                notebook_session.status = "running"
            else:
                notebook_session.status = "starting"
        except Exception as e:
            notebook_session.status = "failed"
            logger.error("Failed to launch notebook session %d: %s", notebook_session.id, e)

        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="notebook_session",
            entity_id=notebook_session.id,
            action="launch",
            details={
                "session_type": session_type,
                "resource_profile": resource_profile,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "status": notebook_session.status,
            },
        )
        return notebook_session

    @staticmethod
    async def stop_session(session: AsyncSession, session_id: int, user_id: int) -> NotebookSession:
        notebook_session = await NotebookService.get_session(session, session_id)
        if not notebook_session:
            raise ValueError("Session not found")

        old_status = notebook_session.status

        # Terminate via the notebook adapter
        if notebook_session.slurm_job_id or notebook_session.k8s_pod_name:
            try:
                notebook_adapter = get_notebook_adapter()
                await notebook_adapter.terminate_session(
                    notebook_session.slurm_job_id or "",
                    pod_name=notebook_session.k8s_pod_name or "",
                    namespace=notebook_session.k8s_namespace or "bioaf-notebooks",
                    gcs_home_prefix=notebook_session.gcs_home_prefix or "",
                )
            except Exception as e:
                logger.warning("Failed to terminate session %s: %s", notebook_session.slurm_job_id, e)

        notebook_session.status = "stopped"
        notebook_session.stopped_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="notebook_session",
            entity_id=notebook_session.id,
            action="stop",
            details={"session_type": notebook_session.session_type},
            previous_value={"status": old_status},
        )
        return notebook_session

    @staticmethod
    async def list_sessions(
        session: AsyncSession,
        org_id: int,
        user_id: int | None = None,
        session_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[NotebookSession], int]:
        query = (
            select(NotebookSession)
            .options(selectinload(NotebookSession.user), selectinload(NotebookSession.experiment))
            .where(NotebookSession.organization_id == org_id)
        )
        count_query = select(func.count(NotebookSession.id)).where(NotebookSession.organization_id == org_id)

        if user_id:
            query = query.where(NotebookSession.user_id == user_id)
            count_query = count_query.where(NotebookSession.user_id == user_id)
        if session_type:
            query = query.where(NotebookSession.session_type == session_type)
            count_query = count_query.where(NotebookSession.session_type == session_type)
        if status:
            query = query.where(NotebookSession.status == status)
            count_query = count_query.where(NotebookSession.status == status)

        query = query.order_by(NotebookSession.created_at.desc())

        result = await session.execute(query)
        sessions = list(result.scalars().all())

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return sessions, total

    @staticmethod
    async def get_session(session: AsyncSession, session_id: int) -> NotebookSession | None:
        result = await session.execute(
            select(NotebookSession)
            .options(selectinload(NotebookSession.user), selectinload(NotebookSession.experiment))
            .where(NotebookSession.id == session_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def check_idle_sessions(session: AsyncSession, idle_timeout_hours: int = 4) -> None:
        """Background task: check for idle sessions and auto-stop them."""
        try:
            result = await session.execute(
                select(NotebookSession).where(NotebookSession.status.in_(["running", "idle"]))
            )
            active_sessions = list(result.scalars().all())

            now = datetime.now(timezone.utc)

            for ns in active_sessions:
                if ns.status == "idle" and ns.idle_since:
                    idle_duration = (now - ns.idle_since).total_seconds() / 3600
                    if idle_duration >= idle_timeout_hours:
                        logger.info("Auto-stopping idle session %d (idle %.1fh)", ns.id, idle_duration)
                        asyncio.create_task(
                            event_bus.emit(
                                SESSION_IDLE,
                                {
                                    "event_type": SESSION_IDLE,
                                    "org_id": ns.organization_id,
                                    "user_id": ns.user_id,
                                    "target_user_id": ns.user_id,
                                    "entity_type": "notebook_session",
                                    "entity_id": ns.id,
                                    "title": f"Idle session auto-stopped after {idle_duration:.1f}h",
                                    "message": f"Your {ns.session_type} session was stopped due to inactivity",
                                    "summary": f"Notebook session {ns.id} auto-stopped (idle {idle_duration:.1f}h)",
                                },
                            )
                        )
                        ns.status = "stopped"
                        ns.stopped_at = now
                        if ns.slurm_job_id or ns.k8s_pod_name:
                            try:
                                notebook_adapter = get_notebook_adapter()
                                await notebook_adapter.terminate_session(
                                    ns.slurm_job_id or "",
                                    pod_name=ns.k8s_pod_name or "",
                                    namespace=ns.k8s_namespace or "bioaf-notebooks",
                                    gcs_home_prefix=ns.gcs_home_prefix or "",
                                )
                            except Exception as e:
                                logger.warning("Failed to terminate idle session: %s", e)

            await session.flush()
            await session.commit()
            logger.info("Idle session check completed, checked %d sessions", len(active_sessions))

        except Exception as e:
            logger.error("Idle session check failed: %s", e)
