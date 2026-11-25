"""Work node service for GCE VM compute sessions (ADR-043).

Manages the lifecycle of SSH-accessible work nodes running as GCE VMs
with Packer-built images and conda environments.
"""

import logging
import secrets
import uuid
from datetime import datetime, timezone

from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notebook_session import ComputeSession
from app.models.user import User
from app.platform.platform_config_service import PlatformConfigService
from app.services.audit_service import log_action
from app.services.post_commit_events import defer_event
from app.services.event_types import (
    WORK_NODE_LAUNCHED,
    WORK_NODE_STOPPED,
    WORK_NODE_HEARTBEAT_TIMEOUT,
)
from app.services.machine_types import MACHINE_TYPES, get_machine_type
from app.services.session_bucket import _bucket_filter
from app.adapters.models import ServiceState, TerminationResult
from app.adapters.registry import get_work_node_adapter
from app.adapters.work_nodes.node_ssh import NODE_TRUST_VERSION, STOP_STATE_UNKNOWN, generate_node_identity
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.services.launch_scope import owned_environment_version, resolve_launch_scope
from app import runtime

logger = logging.getLogger("bioaf.work_nodes")

DEFAULT_MAX_WORK_NODES_PER_USER = 2
DEFAULT_IDLE_TIMEOUT_HOURS = 24
DEFAULT_BOOT_DISK_GB = 100
DEFAULT_BOOT_DISK_TYPE = "pd-ssd"

# Nodes that may still be running and so count against the per-user limit and the costs view.
ACTIVE_NODE_STATUSES = ("pending", "starting", "running", "stopping", "stop_failed")
# A Stop claim older than this is treated as abandoned (its worker died) and may be retried.
STOP_CLAIM_TTL = timedelta(minutes=15)
# Background idle cleanup retries a kept node this many times, then leaves it for Retry stop.
MAX_BACKGROUND_STOP_ATTEMPTS = 3


class WorkNodeService:
    @staticmethod
    async def launch_work_node(
        session: AsyncSession,
        user_id: int,
        org_id: int,
        project_id: int | None = None,
        environment_version_id: int = 0,
        machine_type: str = "",
        input_file_ids: list[int] | None = None,
        github_repo_ids: list[int] | None = None,
        experiment_id: int | None = None,
    ) -> ComputeSession:
        # Validate machine type
        runtime.require_admission("work node launch")
        mt = get_machine_type(machine_type)
        if not mt:
            valid_names = sorted(m["name"] for m in MACHINE_TYPES)
            raise ValidationError(f"Invalid machine type: {machine_type}. Valid types: {', '.join(valid_names)}")

        # Validate environment version is ready and is a work_node environment
        try:
            env_version = await owned_environment_version(session, org_id, environment_version_id)
        except NotFoundError:
            raise ValidationError("Environment version not found") from None
        if env_version.status != "ready":
            raise ValidationError(
                f"Environment version must be in ready status (current: {env_version.status}). "
                "Build the environment first."
            )
        if env_version.environment.environment_type != "work_node":
            raise ValidationError(
                "Only work node environments can be used for work nodes. This environment is configured for notebooks."
            )
        scope = await resolve_launch_scope(session, org_id, project_id=project_id, experiment_id=experiment_id)
        project_id, experiment_id = scope.project_id, scope.experiment_id

        # Require session credentials for SSH
        from app.services.session_credential_service import SessionCredentialService

        # The credential row stays locked until 'starting' is committed below, so a concurrent key
        # replacement either finishes first (this launch uses the new key) or sees this launch.
        cred = await SessionCredentialService.lock_for_user(session, user_id)
        if not cred:
            raise ValidationError(
                "Session credentials are required for work nodes. "
                "Please set up your session credentials in your profile settings."
            )

        # The Work Node SSH policy this launch applies (plan 03B, D03-5). With a range, login from
        # outside it is key-only, so the user's key is generated now if they have none.
        from app.services.ssh_access_policy import applied_work_node_policy

        ssh_policy = await applied_work_node_policy(session)
        if ssh_policy.ranges:
            owner = await session.get(User, user_id)
            await SessionCredentialService.ensure_login_key(session, cred, owner.email if owner else "")
        elif cred.ssh_public_key or cred.ssh_private_key:
            await SessionCredentialService.ensure_login_key(session, cred, "")
        login_public_key = cred.ssh_public_key if cred.ssh_private_key else None

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

        # Git server trust for the node's clones (plan 03C): shipped GitHub keys plus approved hosts.
        from app.platform.git_trust import trust_for_repositories

        git_trust = await trust_for_repositories(session, [r["git_ssh_url"] for r in github_repos_data])
        if git_trust.untrusted_hosts:
            logger.warning(
                "A connected code repository needs a security setting: no approved host key for %s",
                ", ".join(sorted(f"{h}:{p}" for h, p in git_trust.untrusted_hosts)),
            )

        # Check concurrent work node quota
        max_nodes = await WorkNodeService._get_max_nodes_per_user(session)
        running_count_result = await session.execute(
            select(func.count(ComputeSession.id)).where(
                ComputeSession.user_id == user_id,
                ComputeSession.session_type == "ssh",
                ComputeSession.status.in_(ACTIVE_NODE_STATUSES),
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
                        "gcs_uri": f.storage_uri,
                        "relative_path": rel_path,
                    }
                )

        # The private route backend sync will use (plan 03B). A failed private route refuses the
        # launch before anything is recorded or provisioned; it never switches to public sync.
        from app.services.backend_sync_route import ensure_backend_sync_route

        route = await ensure_backend_sync_route(session)

        # Create session record
        compute_session = ComputeSession(
            user_id=user_id,
            organization_id=org_id,
            session_type="ssh",
            project_id=project_id,
            experiment_id=experiment_id,
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
            login_key_fingerprint=SessionCredentialService.key_fingerprint(login_public_key),
            ssh_policy_revision=ssh_policy.revision,
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
                # The session's organization, so the readiness poller can scope its update to it.
                "organization_id": compute_session.organization_id,
                "user_id": user_id,
                "machine_type": machine_type,
                "gce_machine_type": mt.get("gce_machine_type", machine_type),
                "ec2_instance_type": mt.get("ec2_instance_type", machine_type),
                "image_uri": env_version.image_uri,
                "input_files": input_files_spec,
                "heartbeat_token": heartbeat_token,
                "session_credentials": {
                    "username": cred.username,
                    "password_hash": cred.password_hash,
                },
                "ssh_public_key": cred.ssh_public_key,
                "ssh_private_key": cred.ssh_private_key,
                # The user's own public key for login to this, their own, node (never another user's).
                "login_public_key": login_public_key,
                "ssh_policy": ssh_policy.as_spec(),
                "github_repos": github_repos_data,
                "git_known_hosts": git_trust.known_hosts,
                "git_untrusted_hosts": sorted(f"{h}:{p}" for h, p in git_trust.untrusted_hosts),
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
            # Durable trust (plan 03A): the backend sync key and the node's server identity are
            # generated and committed with 'starting', before the provider call, so any worker
            # can save this node's outputs after a restart. Only public halves and the node's own
            # host key travel to the node; the sync private key stays encrypted on this row.
            compute_session.sync_route = route["route"]
            vm_spec["sync_route"] = route["route"]
            if route.get("network"):
                vm_spec["network"] = route["network"]
            if route.get("tag"):
                vm_spec["sync_tag"] = route["tag"]
            if route.get("security_group"):
                vm_spec["backend_sync_security_group"] = route["security_group"]

            identity = generate_node_identity()
            compute_session.sync_private_key = identity.sync_private_key
            compute_session.host_public_key = identity.host_public_key
            compute_session.launch_attempt_id = uuid.uuid4().hex
            compute_session.node_trust_version = NODE_TRUST_VERSION
            vm_spec["sync_public_key"] = identity.sync_public_key
            vm_spec["host_private_key"] = identity.host_private_key
            vm_spec["host_public_key"] = identity.host_public_key
            vm_spec["launch_attempt_id"] = compute_session.launch_attempt_id

            compute_session.status = "starting"
            await session.commit()

            result = await adapter.launch_vm(vm_spec)

            compute_session.gce_instance_name = result.instance_name
            compute_session.gce_zone = result.zone
            compute_session.gce_project_id = result.provider_details.get("gcp_project_id")
            compute_session.provider_instance_id = result.provider_details.get("provider_instance_id")
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

        defer_event(
            session,
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

        return compute_session

    @staticmethod
    async def stop_work_node(
        session: AsyncSession,
        session_id: int,
        user_id: int,
        *,
        org_id: int,
    ) -> ComputeSession:
        """Save the node's outputs, then delete it; keep it and say why when either is not confirmed.

        Retry stop is the same call. A Stop already in progress on another request answers 409.
        """
        compute_session = await WorkNodeService.get_work_node(session, session_id, org_id=org_id)
        if not compute_session:
            raise NotFoundError("Work node not found")
        old_status = compute_session.status
        if not await WorkNodeService._claim_stop(session, compute_session):
            raise ConflictError("This work node is already stopping. Try again shortly.")
        await WorkNodeService._finish_stop(session, compute_session, actor_user_id=user_id, old_status=old_status)
        return compute_session

    @staticmethod
    async def _claim_stop(session: AsyncSession, node: ComputeSession) -> bool:
        """Atomically mark the node 'stopping' unless another Stop holds a fresh claim. Commits."""
        now = datetime.now(timezone.utc)
        claim = await session.execute(
            update(ComputeSession)
            .where(
                ComputeSession.id == node.id,
                ComputeSession.organization_id == node.organization_id,
                or_(
                    ComputeSession.status != "stopping",
                    ComputeSession.stop_claimed_at.is_(None),
                    ComputeSession.stop_claimed_at < now - STOP_CLAIM_TTL,
                ),
            )
            .values(status="stopping", stop_claimed_at=now)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if not claim.rowcount:
            return False
        await session.refresh(node)
        return True

    @staticmethod
    async def _bind_unrecorded_launch(session: AsyncSession, node: ComputeSession) -> str | None:
        """Find the VM of a launch whose provider result was never recorded and bind it to the row.

        Returns None when the node is bound, or when the provider lists no VM for it and the launch
        attempt has settled (``LAUNCH_SETTLE_WINDOW``); otherwise a stop failure reason, and the
        node is kept: the provider cannot answer, its list may still lag the create, or what it
        reports does not match this launch attempt.
        """
        from app.services.work_node_readiness import InventoryUnavailable

        try:
            instances = await get_work_node_adapter().inventory_instances()
        except InventoryUnavailable as exc:
            logger.warning("Could not inventory instances to stop work node %d: %s", node.id, exc)
            return STOP_STATE_UNKNOWN
        except Exception:
            logger.exception("Inventory for work node %d raised", node.id)
            return STOP_STATE_UNKNOWN
        mine = [i for i in instances if i.session_id == str(node.id)]
        if not mine:
            from app.services.work_node_readiness import launch_settled

            if launch_settled(node.created_at):
                return None
            logger.info("Work node %d: launch not settled; an empty inventory is not yet proof", node.id)
            return STOP_STATE_UNKNOWN
        if len(mine) != 1 or mine[0].launch_attempt_id != node.launch_attempt_id or not mine[0].zone:
            logger.warning("Work node %d: provider instances do not match its launch attempt", node.id)
            return STOP_STATE_UNKNOWN
        found = mine[0]
        node.gce_instance_name = found.name
        node.gce_zone = found.zone
        if found.provider_instance_id:
            node.provider_instance_id = found.provider_instance_id
        await session.commit()
        return None

    @staticmethod
    async def _finish_stop(
        session: AsyncSession,
        node: ComputeSession,
        *,
        actor_user_id: int,
        old_status: str,
        stopped_event: tuple[str, str, str] | None = None,
    ) -> bool:
        """Run the provider save-then-delete for a claimed node and record the truthful outcome.

        Returns True when the node is stopped. On any kept outcome the node stays 'stop_failed'
        with its sync credential, save obligation and no stopped timestamp or event.
        """
        working_bucket = ""
        working_bucket_value = await PlatformConfigService.get(session, "working_bucket_name")
        if working_bucket_value is not None:
            val = (working_bucket_value or "").strip()
            if val and val != "null":
                working_bucket = val

        terminate_result = TerminationResult()
        if not node.gce_instance_name and node.launch_attempt_id:
            # The launch was committed before the provider call; a restart can lose the result.
            # Only the provider can say whether a VM exists, so ask it before anything is discarded.
            unresolved = await WorkNodeService._bind_unrecorded_launch(session, node)
            if unresolved:
                terminate_result = TerminationResult(status=ServiceState.UNKNOWN, stop_failure=unresolved)
        if node.gce_instance_name and not terminate_result.stop_failure:
            try:
                adapter = get_work_node_adapter()
                terminate_result = await adapter.terminate_vm(
                    node.gce_instance_name,
                    node.gce_zone or "",
                    gcp_project_id=node.gce_project_id or "",
                    session_id=node.id,
                    working_bucket=working_bucket,
                    sync_private_key=node.sync_private_key,
                    host_public_key=node.host_public_key,
                    provider_instance_id=node.provider_instance_id,
                    launch_attempt_id=node.launch_attempt_id,
                    sync_route=node.sync_route,
                )
            except Exception:
                logger.exception("Stopping work node %d raised; keeping it", node.id)
                terminate_result = TerminationResult(status=ServiceState.UNKNOWN, stop_failure=STOP_STATE_UNKNOWN)

        if terminate_result.stop_failure:
            node.status = "stop_failed"
            node.stop_failure = terminate_result.stop_failure
            node.stop_attempts = (node.stop_attempts or 0) + 1
            node.stop_claimed_at = None
            await session.flush()
            await log_action(
                session,
                user_id=actor_user_id,
                entity_type="work_node",
                entity_id=node.id,
                action="stop_failed",
                details={"reason": terminate_result.stop_failure, "attempt": node.stop_attempts},
                previous_value={"status": old_status},
            )
            await session.commit()
            logger.warning("Work node %d kept after Stop: %s", node.id, terminate_result.stop_failure)
            return False

        # Register output files (ADR-040); a retry does not register a file twice.
        output_files = terminate_result.output_files
        if output_files:
            try:
                from app.services.session_output_service import SessionOutputService

                await SessionOutputService.register_outputs(
                    session,
                    session_id=node.id,
                    organization_id=node.organization_id,
                    project_id=node.project_id,
                    experiment_id=node.experiment_id,
                    user_id=node.user_id,
                    gcs_files=[
                        {"filename": o.filename, "gcs_uri": o.storage_uri, "size_bytes": o.size_bytes}
                        for o in output_files
                    ],
                    source_type="work_node_output",
                )
            except Exception as e:
                logger.warning("Output registration failed for work node %d: %s", node.id, e)

        gcs_output_prefix = terminate_result.output_prefix
        if gcs_output_prefix:
            node.gcs_output_prefix = gcs_output_prefix

        # Move outputs from working to results bucket (ADR-040: two-phase)
        if working_bucket and output_files:
            try:
                results_bucket_value = await PlatformConfigService.get(session, "results_bucket_name")
                results_bucket = (results_bucket_value or "").strip() if results_bucket_value is not None else ""
                if results_bucket and results_bucket != "null":
                    from app.services.session_output_service import SessionOutputService

                    final_prefix = await SessionOutputService.move_outputs_to_results_bucket(
                        session,
                        session_id=node.id,
                        working_bucket=working_bucket,
                        results_bucket=results_bucket,
                    )
                    node.gcs_output_prefix = final_prefix
            except Exception as e:
                logger.warning("Failed to move outputs to results bucket for work node %d: %s", node.id, e)

        node.status = "stopped"
        node.stopped_at = datetime.now(timezone.utc)
        node.stop_failure = None
        node.stop_claimed_at = None
        # Saving and deletion are confirmed: the backend sync secret is no longer needed.
        node.sync_private_key = None
        await session.flush()

        await log_action(
            session,
            user_id=actor_user_id,
            entity_type="work_node",
            entity_id=node.id,
            action="stop",
            details={"machine_type": node.machine_type},
            previous_value={"status": old_status},
        )

        event_type, title, message = stopped_event or (
            WORK_NODE_STOPPED,
            "Work node stopped",
            f"Work node {node.id} stopped",
        )
        defer_event(
            session,
            event_type,
            {
                "event_type": event_type,
                "org_id": node.organization_id,
                "user_id": actor_user_id,
                "target_user_id": node.user_id,
                "entity_type": "work_node",
                "entity_id": node.id,
                "title": title,
                "message": message,
                "summary": message if event_type != WORK_NODE_STOPPED else f"Work node {node.id} stopped",
            },
        )
        return True

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
        *,
        org_id: int,
    ) -> ComputeSession | None:
        """The work node with this id in this Organization, or None.

        A node in another Organization is indistinguishable from a missing one.
        """
        result = await session.execute(
            select(ComputeSession)
            .options(
                selectinload(ComputeSession.user),
                selectinload(ComputeSession.project),
            )
            .where(
                ComputeSession.id == session_id,
                ComputeSession.organization_id == org_id,
                ComputeSession.session_type == "ssh",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def record_heartbeat(
        session: AsyncSession,
        session_id: int,
        token: str,
        *,
        ssh_policy_status: str | None = None,
    ) -> bool:
        """Validate heartbeat token and update heartbeat_at. Returns True if valid."""
        result = await session.execute(
            select(ComputeSession).where(
                ComputeSession.id == session_id,
                ComputeSession.session_type == "ssh",
                ComputeSession.status.in_(["running", "starting", "stopping", "stop_failed"]),
            )
        )
        compute_session = result.scalar_one_or_none()
        if not compute_session:
            return False

        if not secrets.compare_digest(compute_session.heartbeat_token or "", token):
            return False

        compute_session.heartbeat_at = datetime.now(timezone.utc)
        # The node's own report of the login policy it applied (plan 03B); unknown values are ignored.
        if ssh_policy_status in ("applied", "failed", "failed_closed"):
            metadata = dict(compute_session.provider_metadata or {})
            if metadata.get("ssh_policy_status") != ssh_policy_status:
                metadata["ssh_policy_status"] = ssh_policy_status
                compute_session.provider_metadata = metadata
        await session.flush()
        return True

    @staticmethod
    async def check_heartbeat_timeouts(
        session: AsyncSession,
        idle_timeout_hours: int | None = None,
    ) -> None:
        """Stop SSH sessions with stale heartbeats, through the same save-then-delete Stop.

        A node whose save failed stays 'stop_failed'; cleanup retries it a bounded number of
        times, then leaves it for its owner's Retry stop.
        """
        try:
            if idle_timeout_hours is None:
                idle_timeout_hours = await WorkNodeService._get_idle_timeout(session)

            result = await session.execute(
                select(ComputeSession).where(
                    ComputeSession.session_type == "ssh",
                    or_(
                        ComputeSession.status.in_(["running", "starting", "stopping"]),
                        (ComputeSession.status == "stop_failed")
                        & (ComputeSession.stop_attempts < MAX_BACKGROUND_STOP_ATTEMPTS),
                    ),
                )
            )
            active_nodes = list(result.scalars().all())
            now = datetime.now(timezone.utc)

            for node in active_nodes:
                if node.status == "stopping":
                    # A Stop someone asked for, interrupted (for example by a restart) after its
                    # claim was committed: resume it once the claim has expired, whatever the
                    # heartbeat, so it ends stopped or stop_failed with Retry stop.
                    claimed = node.stop_claimed_at
                    if claimed is not None and claimed.tzinfo is None:
                        claimed = claimed.replace(tzinfo=timezone.utc)
                    if claimed is not None and claimed >= now - STOP_CLAIM_TTL:
                        continue
                    logger.info("Resuming an interrupted Stop of work node %d", node.id)
                    if await WorkNodeService._claim_stop(session, node):
                        await WorkNodeService._finish_stop(
                            session, node, actor_user_id=node.user_id, old_status="stopping"
                        )
                    continue

                last_beat = node.heartbeat_at or node.started_at
                if not last_beat:
                    continue

                idle_hours = (now - last_beat).total_seconds() / 3600
                if idle_hours < idle_timeout_hours:
                    continue
                logger.info("Stopping work node %d (no heartbeat for %.1fh)", node.id, idle_hours)
                old_status = node.status
                if not await WorkNodeService._claim_stop(session, node):
                    continue
                await WorkNodeService._finish_stop(
                    session,
                    node,
                    actor_user_id=node.user_id,
                    old_status=old_status,
                    stopped_event=(
                        WORK_NODE_HEARTBEAT_TIMEOUT,
                        "Work node auto-stopped (no heartbeat)",
                        f"Work node {node.id} was stopped after {idle_hours:.1f}h without a heartbeat",
                    ),
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
