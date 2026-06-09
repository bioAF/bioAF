"""Work node service for GCE VM compute sessions (ADR-043).

Manages the lifecycle of SSH-accessible work nodes running as GCE VMs
with Packer-built images and conda environments.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notebook_session import ComputeSession
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action
from app.services.event_bus import event_bus
from app.services.event_types import (
    WORK_NODE_LAUNCHED,
    WORK_NODE_STOPPED,
    WORK_NODE_HEARTBEAT_TIMEOUT,
)
from app.services.machine_types import MACHINE_TYPES, get_machine_type
from app.services.session_bucket import _bucket_filter
from app.adapters.models import TerminationResult
from app.adapters.registry import get_work_node_adapter
from app.exceptions import ConflictError, NotFoundError, ValidationError

logger = logging.getLogger("bioaf.work_nodes")

DEFAULT_MAX_WORK_NODES_PER_USER = 2
DEFAULT_IDLE_TIMEOUT_HOURS = 24
DEFAULT_BOOT_DISK_GB = 100
DEFAULT_BOOT_DISK_TYPE = "pd-ssd"


class WorkNodeService:
    @staticmethod
    async def launch_work_node(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        project_id: int,
        environment_version_id: int,
        machine_type: str,
        input_file_ids: list[int] | None = None,
        github_repo_ids: list[int] | None = None,
    ) -> ComputeSession:
        # Validate machine type
        mt = get_machine_type(machine_type)
        if not mt:
            valid_names = sorted(m["name"] for m in MACHINE_TYPES)
            raise ValidationError(f"Invalid machine type: {machine_type}. Valid types: {', '.join(valid_names)}")

        # Validate environment version is ready and is a work_node environment
        from app.models.environment_version import EnvironmentVersion

        ev_result = await session.execute(
            select(EnvironmentVersion)
            .options(selectinload(EnvironmentVersion.environment))
            .where(EnvironmentVersion.id == environment_version_id)
        )
        env_version = ev_result.scalar_one_or_none()
        if not env_version:
            raise ValidationError("Environment version not found")
        if env_version.status != "ready":
            raise ValidationError(
                f"Environment version must be in ready status (current: {env_version.status}). "
                "Build the environment first."
            )
        if env_version.environment.environment_type != "work_node":
            raise ValidationError(
                "Only work node environments can be used for work nodes. This environment is configured for notebooks."
            )

        # Require session credentials for SSH
        from app.services.session_credential_service import SessionCredentialService

        cred = await SessionCredentialService.get_by_user_id(session, user_id)
        if not cred:
            raise ValidationError(
                "Session credentials are required for work nodes. "
                "Please set up your session credentials in your profile settings."
            )

        # Validate and fetch GitHub repos
        github_repos_data: list[dict] = []
        if github_repo_ids:
            from app.models.github_repo import GitHubRepo

            repo_result = await session.execute(
                select(GitHubRepo).where(
                    GitHubRepo.id.in_(github_repo_ids),
                    GitHubRepo.user_id == user_id,
                )
            )
            repos = list(repo_result.scalars().all())
            found_ids = {r.id for r in repos}
            missing = set(github_repo_ids) - found_ids
            if missing:
                raise ValidationError(f"GitHub repos not found: {missing}")
            github_repos_data = [{"git_ssh_url": r.git_ssh_url, "display_name": r.display_name} for r in repos]

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
            raise ConflictError(
                f"Concurrent work node limit reached ({max_nodes}). "
                "Stop an existing work node before launching a new one."
            )

        # Generate heartbeat token
        heartbeat_token = secrets.token_urlsafe(32)

        # Validate and resolve input files
        input_files_spec: list[dict] = []
        if input_file_ids:
            from app.models.file import File
            from app.services.notebook_service import _resolve_input_file_context, _build_relative_path

            file_results = await session.execute(select(File).where(File.id.in_(input_file_ids)))
            found_files = {f.id: f for f in file_results.scalars().all()}

            name_cache = await _resolve_input_file_context(session, found_files)

            for fid in input_file_ids:
                f = found_files.get(fid)
                if not f or f.organization_id != org_id:
                    raise ValidationError(f"File {fid} not found or not accessible")
                rel_path = _build_relative_path(f, name_cache)
                input_files_spec.append(
                    {
                        "file_id": f.id,
                        "gcs_uri": f.gcs_uri,
                        "relative_path": rel_path,
                    }
                )

        # Create session record
        compute_session = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type="ssh",
            project_id=project_id,
            environment_version_id=environment_version_id,
            machine_type=machine_type,
            data_mount_paths=input_file_ids or [],
            github_repo_ids=github_repo_ids or [],
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

        # Look up working bucket and SA email for GCS mounts
        config_map = await PlatformConfigService.get_many(
            session,
            [
                "working_bucket_name",
                "notebook_runner_sa_email",
                "gcp_project_id",
                "gcp_zone",
                "work_node_boot_disk_gb",
                "work_node_boot_disk_type",
            ],
        )

        # Extract conda env name from definition
        conda_env_name = "bioaf"
        if env_version.definition_format == "conda":
            try:
                import yaml

                data = yaml.safe_load(env_version.definition_content)
                conda_env_name = data.get("name", "bioaf") if data else "bioaf"
            except Exception:
                pass

        # Build environment label for MOTD
        env_label = f"{env_version.environment.name} v{env_version.version_number}.{env_version.build_number}"

        # Launch via GCE adapter
        try:
            adapter = get_work_node_adapter()
            vm_spec: dict = {
                "session_id": compute_session.id,
                "user_id": user_id,
                "machine_type": machine_type,
                "gce_machine_type": mt.get("gce_machine_type", machine_type),
                "image_uri": env_version.image_uri,
                "input_files": input_files_spec,
                "heartbeat_token": heartbeat_token,
                "session_credentials": {
                    "username": cred.username,
                    "password_hash": cred.password_hash,
                },
                "ssh_public_key": cred.ssh_public_key,
                "ssh_private_key": cred.ssh_private_key,
                "github_repos": github_repos_data,
                "conda_env_name": conda_env_name,
                "environment_label": env_label,
            }

            bucket_name = (config_map.get("working_bucket_name") or "").strip()
            if bucket_name and bucket_name != "null":
                vm_spec["working_bucket"] = bucket_name

            sa_email = (config_map.get("notebook_runner_sa_email") or "").strip()
            if sa_email and sa_email != "null":
                vm_spec["service_account_email"] = sa_email

            gcp_project = (config_map.get("gcp_project_id") or "").strip()
            if gcp_project and gcp_project != "null":
                vm_spec["gcp_project_id"] = gcp_project

            gcp_zone = (config_map.get("gcp_zone") or "").strip()
            if gcp_zone and gcp_zone != "null":
                vm_spec["gcp_zone"] = gcp_zone

            disk_gb_raw = (config_map.get("work_node_boot_disk_gb") or "").strip()
            try:
                vm_spec["boot_disk_gb"] = (
                    int(disk_gb_raw) if disk_gb_raw and disk_gb_raw != "null" else DEFAULT_BOOT_DISK_GB
                )
            except ValueError:
                vm_spec["boot_disk_gb"] = DEFAULT_BOOT_DISK_GB
            compute_session.requested_disk_gb = vm_spec["boot_disk_gb"]

            disk_type = (config_map.get("work_node_boot_disk_type") or "").strip()
            vm_spec["boot_disk_type"] = disk_type if disk_type and disk_type != "null" else DEFAULT_BOOT_DISK_TYPE

            # GPU accelerator info
            if mt.get("accelerator_type"):
                vm_spec["accelerator_type"] = mt["accelerator_type"]
                vm_spec["accelerator_count"] = mt.get("accelerator_count", 1)

            # Commit 'starting' before launching. The GCE adapter kicks off a
            # background readiness poll that can mark the session 'running' (on
            # its own DB session) within seconds. If the row is not committed
            # first, that poll's UPDATE matches no row; and if this request
            # re-writes status afterward, it clobbers the poll's 'running' back
            # to 'starting', leaving the node stuck in 'starting' though the VM
            # is up.
            compute_session.status = "starting"
            await session.commit()

            result = await adapter.launch_vm(vm_spec)

            compute_session.gce_instance_name = result.instance_name
            compute_session.gce_zone = result.zone
            compute_session.gce_project_id = result.provider_details.get("gcp_project_id")
            adapter_status = result.status
            if adapter_status == "error":
                compute_session.status = "failed"
            elif adapter_status == "running":
                # Synchronous adapters (e.g. local/mock) have no poll; honor the
                # status they report immediately.
                compute_session.status = "running"
            # adapter_status == "starting": leave the start -> running/failed
            # transition to the background readiness poll, do not overwrite it.
            if result.access_url:
                compute_session.access_url = result.access_url
        except Exception as e:
            from app.adapters.failure_classification import classify_gce_vm_failure

            compute_session.status = "failed"
            reason, message = classify_gce_vm_failure(str(e))
            compute_session.failure_reason = reason
            compute_session.failure_message = message
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
                "github_repo_ids": github_repo_ids or [],
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
            raise NotFoundError("Work node not found")

        old_status = compute_session.status

        # Set status to "stopping" and commit immediately so the UI
        # reflects the in-progress state even if the request times out.
        compute_session.status = "stopping"
        await session.flush()
        await session.commit()

        # Look up working bucket for output sync
        working_bucket = ""
        working_bucket_value = await PlatformConfigService.get(session, "working_bucket_name")
        if working_bucket_value is not None:
            val = (working_bucket_value or "").strip()
            if val and val != "null":
                working_bucket = val

        terminate_result = TerminationResult()
        if compute_session.gce_instance_name:
            try:
                adapter = get_work_node_adapter()
                terminate_result = await adapter.terminate_vm(
                    compute_session.gce_instance_name,
                    compute_session.gce_zone or "",
                    gcp_project_id=compute_session.gce_project_id or "",
                    session_id=compute_session.id,
                    working_bucket=working_bucket,
                )
            except Exception as e:
                logger.warning("Failed to terminate work node %d: %s", session_id, e)

        # Register output files (ADR-040)
        output_files = terminate_result.output_files
        if output_files:
            try:
                from app.services.session_output_service import SessionOutputService

                await SessionOutputService.register_outputs(
                    session,
                    session_id=compute_session.id,
                    organization_id=compute_session.organization_id,
                    project_id=compute_session.project_id,
                    experiment_id=compute_session.experiment_id,
                    user_id=compute_session.user_id,
                    gcs_files=[
                        {"filename": o.filename, "gcs_uri": o.storage_uri, "size_bytes": o.size_bytes}
                        for o in output_files
                    ],
                    source_type="work_node_output",
                )
            except Exception as e:
                logger.warning("Output registration failed for work node %d: %s", session_id, e)

        gcs_output_prefix = terminate_result.output_prefix
        if gcs_output_prefix:
            compute_session.gcs_output_prefix = gcs_output_prefix

        # Move outputs from working to results bucket (ADR-040: two-phase)
        if working_bucket and output_files:
            try:
                results_bucket_value = await PlatformConfigService.get(session, "results_bucket_name")
                results_bucket = (results_bucket_value or "").strip() if results_bucket_value is not None else ""
                if results_bucket and results_bucket != "null":
                    from app.services.session_output_service import SessionOutputService

                    final_prefix = await SessionOutputService.move_outputs_to_results_bucket(
                        session,
                        session_id=compute_session.id,
                        working_bucket=working_bucket,
                        results_bucket=results_bucket,
                    )
                    compute_session.gcs_output_prefix = final_prefix
            except Exception as e:
                logger.warning("Failed to move outputs to results bucket for work node %d: %s", session_id, e)

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
        bucket: str | None = None,
    ) -> tuple[list[ComputeSession], int]:
        query = (
            select(ComputeSession)
            .options(
                selectinload(ComputeSession.user),
                selectinload(ComputeSession.project),
            )
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

        bucket_filter = _bucket_filter(ComputeSession, bucket)
        if bucket_filter is not None:
            query = query.where(bucket_filter)
            count_query = count_query.where(bucket_filter)

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
            .options(
                selectinload(ComputeSession.user),
                selectinload(ComputeSession.project),
            )
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
        """Terminate SSH sessions with stale heartbeats."""
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

                    if node.gce_instance_name:
                        try:
                            adapter = get_work_node_adapter()
                            await adapter.terminate_vm(
                                node.gce_instance_name,
                                node.gce_zone or "",
                                gcp_project_id=node.gce_project_id or "",
                                session_id=node.id,
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
        value = await PlatformConfigService.get(session, "work_node_max_per_user")
        if value is not None:
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
        return DEFAULT_MAX_WORK_NODES_PER_USER

    @staticmethod
    async def _get_idle_timeout(session: AsyncSession) -> int:
        value = await PlatformConfigService.get(session, "work_node_idle_timeout_hours")
        if value is not None:
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
        return DEFAULT_IDLE_TIMEOUT_HOURS
