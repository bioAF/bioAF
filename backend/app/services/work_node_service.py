"""Work node service for SSH-accessible compute sessions (ADR-034)."""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notebook_session import ComputeSession
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    WORK_NODE_LAUNCHED,
    WORK_NODE_STOPPED,
    WORK_NODE_HEARTBEAT_TIMEOUT,
)
from app.services.machine_types import MACHINE_TYPE_NAMES, get_machine_type
from app.adapters.registry import get_notebook_adapter

logger = logging.getLogger("bioaf.work_nodes")

DEFAULT_MAX_WORK_NODES_PER_USER = 2
DEFAULT_IDLE_TIMEOUT_HOURS = 24


class WorkNodeService:
    @staticmethod
    async def launch_work_node(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        project_id: int,
        environment_version_id: int,
        machine_type: str,
        data_mount_paths: list[str] | None = None,
    ) -> ComputeSession:
        # Validate machine type
        mt = get_machine_type(machine_type)
        if not mt:
            raise ValueError(
                f"Invalid machine type: {machine_type}. Valid types: {', '.join(sorted(MACHINE_TYPE_NAMES))}"
            )

        # Validate environment version is ready
        from app.models.environment_version import EnvironmentVersion

        ev_result = await session.execute(
            select(EnvironmentVersion).where(EnvironmentVersion.id == environment_version_id)
        )
        env_version = ev_result.scalar_one_or_none()
        if not env_version:
            raise ValueError("Environment version not found")
        if env_version.status != "ready":
            raise ValueError(
                f"Environment version must be in ready status (current: {env_version.status}). "
                "Build the environment first."
            )

        # Require session credentials for SSH
        from app.services.session_credential_service import SessionCredentialService

        cred = await SessionCredentialService.get_by_user_id(session, user_id)
        if not cred:
            raise ValueError(
                "Session credentials are required for work nodes. "
                "Please set up your session credentials in your profile settings."
            )

        # Check concurrent work node quota
        max_nodes = await WorkNodeService._get_max_nodes_per_user(session)
        running_count_result = await session.execute(
            select(func.count(ComputeSession.id)).where(
                ComputeSession.user_id == user_id,
                ComputeSession.session_type == "ssh",
                ComputeSession.status.in_(["pending", "starting", "running"]),
            )
        )
        running_count = running_count_result.scalar() or 0
        if running_count >= max_nodes:
            raise ValueError(
                f"Concurrent work node limit reached ({max_nodes}). "
                "Stop an existing work node before launching a new one."
            )

        # Generate heartbeat token
        heartbeat_token = secrets.token_urlsafe(32)

        # Create session record
        compute_session = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type="ssh",
            project_id=project_id,
            environment_version_id=environment_version_id,
            machine_type=machine_type,
            data_mount_paths=data_mount_paths or [],
            resource_profile="custom",
            cpu_cores=mt["cpu"],
            memory_gb=mt["memory_gb"],
            status="pending",
            started_at=datetime.now(timezone.utc),
            heartbeat_token=heartbeat_token,
            heartbeat_at=datetime.now(timezone.utc),
        )
        session.add(compute_session)
        await session.flush()

        # Launch via adapter
        try:
            adapter = get_notebook_adapter()
            spec: dict = {
                "session_type": "ssh",
                "resource_profile": "custom",
                "cpu_cores": mt["cpu"],
                "memory_gb": mt["memory_gb"],
                "user_id": user_id,
                "session_id": compute_session.id,
                "image": env_version.image_uri,
                "machine_type": machine_type,
                "data_mount_paths": data_mount_paths or [],
                "node_pool": mt["node_pool"],
                "gpu": mt["gpu"],
                "heartbeat_token": heartbeat_token,
                "session_credentials": {
                    "username": cred.username,
                    "password_hash": cred.password_hash,
                },
            }

            result = await adapter.launch_session(spec)

            compute_session.slurm_job_id = str(result.get("session_id", ""))
            compute_session.k8s_pod_name = result.get("pod_name")
            compute_session.k8s_namespace = result.get("namespace")
            compute_session.access_url = result.get("access_url")
            compute_session.gcs_home_prefix = result.get("gcs_home_prefix")
            adapter_status = result.get("status", "starting")
            if adapter_status == "error":
                compute_session.status = "failed"
            elif adapter_status == "running":
                compute_session.status = "running"
            else:
                compute_session.status = "starting"
        except Exception as e:
            compute_session.status = "failed"
            logger.error("Failed to launch work node %d: %s", compute_session.id, e)

        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="work_node",
            entity_id=compute_session.id,
            action="launch",
            details={
                "machine_type": machine_type,
                "environment_version_id": environment_version_id,
                "project_id": project_id,
                "status": compute_session.status,
            },
        )

        asyncio.create_task(
            event_bus.emit(
                WORK_NODE_LAUNCHED,
                {
                    "event_type": WORK_NODE_LAUNCHED,
                    "org_id": org_id,
                    "user_id": user_id,
                    "target_user_id": user_id,
                    "entity_type": "work_node",
                    "entity_id": compute_session.id,
                    "title": "Work node launched",
                    "message": f"Work node launched with {machine_type}",
                    "summary": f"Work node {compute_session.id} launched",
                },
            )
        )

        return compute_session

    @staticmethod
    async def stop_work_node(
        session: AsyncSession,
        session_id: int,
        user_id: int,
    ) -> ComputeSession:
        compute_session = await WorkNodeService.get_work_node(session, session_id)
        if not compute_session:
            raise ValueError("Work node not found")

        old_status = compute_session.status

        if compute_session.k8s_pod_name:
            try:
                adapter = get_notebook_adapter()
                await adapter.terminate_session(
                    compute_session.slurm_job_id or "",
                    pod_name=compute_session.k8s_pod_name or "",
                    namespace=compute_session.k8s_namespace or "bioaf-notebooks",
                    gcs_home_prefix=compute_session.gcs_home_prefix or "",
                )
            except Exception as e:
                logger.warning("Failed to terminate work node %d: %s", session_id, e)

        compute_session.status = "stopped"
        compute_session.stopped_at = datetime.now(timezone.utc)
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="work_node",
            entity_id=compute_session.id,
            action="stop",
            details={"machine_type": compute_session.machine_type},
            previous_value={"status": old_status},
        )

        asyncio.create_task(
            event_bus.emit(
                WORK_NODE_STOPPED,
                {
                    "event_type": WORK_NODE_STOPPED,
                    "org_id": compute_session.organization_id,
                    "user_id": user_id,
                    "target_user_id": compute_session.user_id,
                    "entity_type": "work_node",
                    "entity_id": compute_session.id,
                    "title": "Work node stopped",
                    "message": f"Work node {compute_session.id} stopped",
                    "summary": f"Work node {compute_session.id} stopped",
                },
            )
        )

        return compute_session

    @staticmethod
    async def list_work_nodes(
        session: AsyncSession,
        org_id: int,
        user_id: int | None = None,
        status: str | None = None,
    ) -> tuple[list[ComputeSession], int]:
        query = (
            select(ComputeSession)
            .options(selectinload(ComputeSession.user))
            .where(
                ComputeSession.organization_id == org_id,
                ComputeSession.session_type == "ssh",
            )
        )
        count_query = select(func.count(ComputeSession.id)).where(
            ComputeSession.organization_id == org_id,
            ComputeSession.session_type == "ssh",
        )

        if user_id:
            query = query.where(ComputeSession.user_id == user_id)
            count_query = count_query.where(ComputeSession.user_id == user_id)
        if status:
            query = query.where(ComputeSession.status == status)
            count_query = count_query.where(ComputeSession.status == status)

        query = query.order_by(ComputeSession.created_at.desc())

        result = await session.execute(query)
        sessions = list(result.scalars().all())

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return sessions, total

    @staticmethod
    async def get_work_node(
        session: AsyncSession,
        session_id: int,
    ) -> ComputeSession | None:
        result = await session.execute(
            select(ComputeSession)
            .options(selectinload(ComputeSession.user))
            .where(ComputeSession.id == session_id, ComputeSession.session_type == "ssh")
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def record_heartbeat(
        session: AsyncSession,
        session_id: int,
        token: str,
    ) -> bool:
        """Validate heartbeat token and update heartbeat_at. Returns True if valid."""
        result = await session.execute(
            select(ComputeSession).where(
                ComputeSession.id == session_id,
                ComputeSession.session_type == "ssh",
                ComputeSession.status.in_(["running", "starting"]),
            )
        )
        compute_session = result.scalar_one_or_none()
        if not compute_session:
            return False

        if not secrets.compare_digest(compute_session.heartbeat_token or "", token):
            return False

        compute_session.heartbeat_at = datetime.now(timezone.utc)
        await session.flush()
        return True

    @staticmethod
    async def check_heartbeat_timeouts(
        session: AsyncSession,
        idle_timeout_hours: int | None = None,
    ) -> None:
        """Terminate SSH sessions with stale heartbeats.

        Reads idle_timeout_hours from platform_config if not provided.
        """
        try:
            if idle_timeout_hours is None:
                idle_timeout_hours = await WorkNodeService._get_idle_timeout(session)

            result = await session.execute(
                select(ComputeSession).where(
                    ComputeSession.session_type == "ssh",
                    ComputeSession.status.in_(["running", "starting"]),
                )
            )
            active_nodes = list(result.scalars().all())
            now = datetime.now(timezone.utc)

            for node in active_nodes:
                last_beat = node.heartbeat_at or node.started_at
                if not last_beat:
                    continue

                idle_hours = (now - last_beat).total_seconds() / 3600
                if idle_hours >= idle_timeout_hours:
                    logger.info(
                        "Terminating work node %d (no heartbeat for %.1fh)",
                        node.id,
                        idle_hours,
                    )

                    if node.k8s_pod_name:
                        try:
                            adapter = get_notebook_adapter()
                            await adapter.terminate_session(
                                node.slurm_job_id or "",
                                pod_name=node.k8s_pod_name or "",
                                namespace=node.k8s_namespace or "bioaf-notebooks",
                                gcs_home_prefix=node.gcs_home_prefix or "",
                            )
                        except Exception as e:
                            logger.warning("Failed to terminate stale work node %d: %s", node.id, e)

                    node.status = "stopped"
                    node.stopped_at = now

                    asyncio.create_task(
                        event_bus.emit(
                            WORK_NODE_HEARTBEAT_TIMEOUT,
                            {
                                "event_type": WORK_NODE_HEARTBEAT_TIMEOUT,
                                "org_id": node.organization_id,
                                "user_id": node.user_id,
                                "target_user_id": node.user_id,
                                "entity_type": "work_node",
                                "entity_id": node.id,
                                "title": "Work node auto-stopped (no heartbeat)",
                                "message": (
                                    f"Work node {node.id} was stopped after {idle_hours:.1f}h without a heartbeat"
                                ),
                                "summary": f"Work node {node.id} heartbeat timeout",
                            },
                        )
                    )

            await session.flush()
            await session.commit()

            if active_nodes:
                logger.info("Heartbeat check: %d SSH sessions checked", len(active_nodes))

        except Exception as e:
            logger.error("Heartbeat timeout check failed: %s", e)

    @staticmethod
    async def _get_max_nodes_per_user(session: AsyncSession) -> int:
        result = await session.execute(text("SELECT value FROM platform_config WHERE key = 'work_node_max_per_user'"))
        row = result.first()
        if row:
            try:
                return int(row[0])
            except (ValueError, TypeError):
                pass
        return DEFAULT_MAX_WORK_NODES_PER_USER

    @staticmethod
    async def _get_idle_timeout(session: AsyncSession) -> int:
        result = await session.execute(
            text("SELECT value FROM platform_config WHERE key = 'work_node_idle_timeout_hours'")
        )
        row = result.first()
        if row:
            try:
                return int(row[0])
            except (ValueError, TypeError):
                pass
        return DEFAULT_IDLE_TIMEOUT_HOURS
