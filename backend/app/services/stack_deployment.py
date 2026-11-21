"""Phase 19 - Stack deployment service.

Orchestrates full stack deployment (storage + compute) and teardown.
Provides cluster status via GKE API.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from functools import partial
from typing import AsyncGenerator

import anyio
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.kubernetes.cluster_location import is_gke_location
from app.exceptions import ConflictError, StateError, ValidationError
from app.services.activity_feed_service import ActivityFeedService
from app.services.audit_service import log_action
from app.services.component_queue import process_queued_components
from app.services.deployment_outputs import (
    COMPUTE_AWS_OUTPUTS,
    COMPUTE_GCP_OUTPUTS,
    COMPUTE_OUTPUTS,
    COMPUTE_TEARDOWN_CLEARED_KEYS,
    IMAGE_BUILD_OUTPUTS,
    STORAGE_BUCKETS_TO_EMPTY,
    STORAGE_DEPLOY_OUTPUTS,
    STORAGE_DESTROY_CLEARED_KEYS,
    STORAGE_SYNC_OUTPUTS,
    BlankOutputs,
    persist_outputs,
)
from app.services.orphaned_resource_service import OrphanedResourceService
from app.services.stack_progress import StackProgress, reduce_progress
from app.services.storage_generation import ensure_storage_generation, retire_storage_generation
from app.services.terraform_executor import RUN_INTERRUPTED_MESSAGE, TerraformExecutor, TerraformProgressEvent
from app.services.terraform_stream import INTERRUPTED_EVENT, OperationOutcome, record_whole
from app.adapters.work_nodes.gce_capacity import AllZonesExhaustedError

logger = logging.getLogger("bioaf.stack_deployment")


# -----------------------------------------------------------------------
# Pydantic models for cluster status
# -----------------------------------------------------------------------


class NodePoolStatus(BaseModel):
    name: str
    machine_type: str
    min_nodes: int
    max_nodes: int
    current_nodes: int
    spot: bool
    status: str


class ClusterInfo(BaseModel):
    cluster_name: str
    status: str
    node_count: int
    pipeline_pool: NodePoolStatus
    interactive_pool: NodePoolStatus


class StackStatus(BaseModel):
    compute_stack: str | None
    compute_deployed: bool
    storage_deployed: bool
    pubsub_configured: bool = False
    cluster: ClusterInfo | None = None
    has_orphaned_clusters: bool = False
    # Compute is deployed but its live detail could not be read: "cluster_location_unknown",
    # "provider_denied" (bioAF's cloud access), or "provider_unavailable" (try again later).
    cluster_unavailable: str | None = None


async def _get_gke_credentials(session: AsyncSession):
    """Read SA credentials from platform_config for GKE API calls.

    Returns a credentials object (from the Credentials seam) or None to fall back
    to ADC. Only the legacy service_account_key path resolves explicit creds;
    vm_default returns None so the GKE client uses the VM's attached identity.
    Same pattern as GcsStorageService.get_credentials().
    """
    from app.adapters.credentials import get_credentials_provider
    from app.platform.platform_config_service import PlatformConfigService

    config = await PlatformConfigService.get_many(
        session,
        ["gcp_credential_source", "gcp_service_account_key"],
    )

    if config.get("gcp_credential_source") != "service_account_key":
        return None

    key_json = config.get("gcp_service_account_key")
    if not key_json or key_json == "null":
        return None

    try:
        return get_credentials_provider().load_credentials(config)
    except Exception as e:
        logger.warning("Failed to load GKE credentials from platform_config: %s", e)
        return None


async def _select_default_pool_zone(session: AsyncSession, compute_region: str | None) -> str:
    """Run the pre-flight capacity probe and return the winning zone.

    Tests patch this helper to skip the real GCE round-trip. Production
    deploys hit the probe which iterates the regional zones and returns
    the first one with e2-medium capacity. Raises AllZonesExhaustedError
    if every candidate zone is stocked out.
    """
    from app.adapters.registry import get_work_node_adapter
    from app.gcp_zones import zones_for_region

    probe_region = compute_region or (await _read_config(session, "gcp_region")) or "us-central1"
    candidate_zones = zones_for_region(probe_region)
    # The work-node adapter owns GCE access and resolves project + credentials
    # itself (Phase 6); capacity probing is no longer a service-level concern.
    return await get_work_node_adapter().probe_zone_capacity(candidate_zones)


async def get_cluster_status(session: AsyncSession) -> StackStatus:
    """Get the current stack and cluster status.

    If compute is deployed, queries the GKE API for live cluster info.
    """
    compute_deployed = await _read_config(session, "compute_deployed")
    compute_stack_val = await _read_config(session, "compute_stack")
    storage_deployed = await _read_config(session, "storage_deployed")
    pubsub_topic = await _read_config(session, "pubsub_topic_name")

    is_deployed = compute_deployed == "true"
    stack = compute_stack_val if compute_stack_val != "null" else None
    storage = storage_deployed == "true"
    pubsub = pubsub_topic not in ("null", "")

    # Check for unresolved orphaned GKE clusters
    orphan_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM orphaned_resources "
            "WHERE resource_type = 'gke_cluster' AND status IN ('detected', 'failed')"
        )
    )
    has_orphans = (orphan_result.scalar() or 0) > 0

    if not is_deployed:
        return StackStatus(
            compute_stack=stack,
            compute_deployed=False,
            storage_deployed=storage,
            pubsub_configured=pubsub,
            cluster=None,
            has_orphaned_clusters=has_orphans,
        )

    # Query the cluster control plane for live detail (cloud-selected adapter
    # owns the GKE/EKS read and resolves its own identity).
    try:
        from app.adapters.registry import get_compute_adapter

        detail = await get_compute_adapter().get_cluster_detail()

        pipeline_pool = None
        interactive_pool = None

        for pool in detail.node_pools:
            pool_info = NodePoolStatus(
                name=pool.name,
                machine_type=pool.machine_type,
                min_nodes=pool.min_nodes,
                max_nodes=pool.max_nodes,
                current_nodes=pool.current_nodes,
                spot=pool.spot,
                status=pool.status,
            )
            if "pipeline" in pool.name:
                pipeline_pool = pool_info
            elif "interactive" in pool.name:
                interactive_pool = pool_info

        # Fallback if pools not found
        if not pipeline_pool:
            pipeline_pool = NodePoolStatus(
                name="bioaf-pipelines",
                machine_type="unknown",
                min_nodes=0,
                max_nodes=0,
                current_nodes=0,
                spot=False,
                status="UNKNOWN",
            )
        if not interactive_pool:
            interactive_pool = NodePoolStatus(
                name="bioaf-interactive",
                machine_type="unknown",
                min_nodes=0,
                max_nodes=0,
                current_nodes=0,
                spot=False,
                status="UNKNOWN",
            )

        cluster_info = ClusterInfo(
            cluster_name=detail.name,
            status=detail.status,
            node_count=detail.node_count,
            pipeline_pool=pipeline_pool,
            interactive_pool=interactive_pool,
        )

        return StackStatus(
            compute_stack=stack,
            compute_deployed=True,
            storage_deployed=storage,
            pubsub_configured=pubsub,
            cluster=cluster_info,
            has_orphaned_clusters=has_orphans,
        )

    except Exception as exc:
        from app.adapters.kubernetes.cluster_location import ClusterLocationUnknown, classify_provider_failure

        if isinstance(exc, ClusterLocationUnknown):
            reason = "cluster_location_unknown"
        else:
            reason = "provider_denied" if classify_provider_failure(exc) == "denied" else "provider_unavailable"
        logger.error("Failed to query GKE cluster status (%s, error_type=%s)", reason, type(exc).__name__)
        return StackStatus(
            compute_stack=stack,
            compute_deployed=True,
            storage_deployed=storage,
            pubsub_configured=pubsub,
            cluster=None,
            has_orphaned_clusters=has_orphans,
            cluster_unavailable=reason,
        )


async def _deployed_cluster_location(session: AsyncSession) -> str | None:
    """Where the deployed GKE cluster is, for recording it as an orphan.

    The recorded location, else discovered through the compute adapter, else None (unknown).
    Never the installation default: probing a guessed region reports NOT_FOUND, which recovery
    would read as "already gone" while the cluster keeps running elsewhere.
    """
    recorded = await _read_config(session, "gke_cluster_location")
    if is_gke_location(recorded):
        return recorded
    from app.adapters.registry import get_compute_adapter

    try:
        return await get_compute_adapter().deployed_cluster_location()
    except Exception as exc:
        logger.warning(
            "Deployed GKE cluster location unknown (error_type=%s); recording the orphan without one",
            type(exc).__name__,
        )
    return None


async def _read_config(session: AsyncSession, key: str) -> str:
    """Read a single platform_config value, defaulting to 'null'."""
    from app.platform.platform_config_service import PlatformConfigService

    val = await PlatformConfigService.get(session, key)
    return val if val is not None else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a platform_config key."""
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, key, value)


async def _run_module(
    session: AsyncSession, user_id: int, module_name: str
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Run plan + apply for a Terraform module, yielding progress events.

    Commits the finished plan so pollers can see it; the apply only flushes. An
    interrupted plan is flushed too, and the session owner commits it, so this
    never commits work the caller left pending.

    This is the real implementation. Tests mock this function.
    """
    try:
        run = await TerraformExecutor.run_plan(session, user_id, module_name=module_name)
    except asyncio.CancelledError:
        # Connection dropped during plan -- mark any active run as failed
        logger.warning("Plan cancelled for module %s (client disconnected)", module_name)
        await _mark_plan_interrupted(session, module_name)
        yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
        return
    await session.commit()

    if run.status != "awaiting_confirmation":
        yield TerraformProgressEvent(
            event_type="apply_error",
            message=run.error_message or f"Plan failed for {module_name}",
        )
        return

    async with aclosing(TerraformExecutor.run_apply(session, run.id, user_id)) as events:
        async for event in events:
            yield event


async def _mark_plan_interrupted(session: AsyncSession, module_name: str) -> None:
    """Fail the module's active run, if the interrupted transaction allows it."""
    with anyio.CancelScope(shield=True):
        try:
            await session.execute(
                text("""
                UPDATE terraform_runs
                SET status = 'failed',
                    error_message = :message,
                    completed_at = now()
                WHERE status IN ('planning', 'applying')
                  AND module_name = :mod
                """).bindparams(mod=module_name, message=RUN_INTERRUPTED_MESSAGE)
            )
            await session.flush()
        except Exception:
            logger.warning("Could not record the interrupted plan for %s", module_name, exc_info=True)


async def _run_destroy(
    session: AsyncSession, user_id: int, module_name: str
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Run destroy for a Terraform module. Tests mock this function."""
    async with aclosing(TerraformExecutor.run_destroy(session, user_id, module_name)) as events:
        async for event in events:
            yield event


@dataclass
class _PhaseRun:
    """Where one deploy phase started, and what it ended with."""

    progress: StackProgress
    outcome: OperationOutcome = field(default_factory=OperationOutcome)


async def _deploy_phase(
    session: AsyncSession,
    user_id: int,
    module_name: str,
    phase: _PhaseRun,
    *,
    tag_deploy_phase: bool,
    complete_message: str,
    after_apply: Callable[[dict], Awaitable[None]],
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Run one module, reporting stack totals and filling in ``phase``.

    ``apply_complete`` runs the module's post-hook and becomes ``phase_complete``,
    so the frontend does not treat a module finishing as the end of the stack. An
    interruption is left for deploy_stack to report once restoration has run.
    """
    phase_tagged = not tag_deploy_phase
    async with aclosing(_run_module(session, user_id, module_name)) as events:
        async for event in events:
            phase.outcome.observe(event)
            if event.event_type == INTERRUPTED_EVENT:
                continue
            if not phase_tagged:
                await _tag_deploy_phase(session, module_name)
                phase_tagged = True

            phase.progress, shown = reduce_progress(phase.progress, module_name, event)
            if event.event_type == "apply_complete":
                # The apply succeeded: record it whole. If that is cancelled the
                # outcome stays uncertain, which a status check or config sync resolves.
                await record_whole(session, partial(after_apply, event.extra.get("outputs", {})))
                yield TerraformProgressEvent(
                    event_type="phase_complete",
                    message=complete_message,
                    resources_completed=phase.progress.completed,
                    resources_total=phase.progress.planned,
                )
            else:
                yield shown


async def _tag_deploy_phase(session: AsyncSession, module_name: str) -> None:
    await session.execute(
        text("""
        UPDATE terraform_runs SET deploy_phase = :phase
        WHERE module_name = :phase
          AND status IN ('planning', 'applying', 'awaiting_confirmation')
        """).bindparams(phase=module_name)
    )
    await session.flush()


async def _after_storage_apply(session: AsyncSession, user_id: int, org_id: int | None, outputs: dict) -> None:
    await persist_outputs(session, outputs, STORAGE_DEPLOY_OUTPUTS, BlankOutputs.SKIP)
    await _set_config(session, "storage_deployed", "true")
    # Storage is now usable; drain queued components that only
    # need the working bucket (notebook + cellxgene image builds
    # can start now while the cluster is still being built).
    await process_queued_components(session)
    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure",
        entity_id=0,
        action="deploy_storage",
        details={"module": "storage", "status": "completed"},
    )
    if org_id is not None:
        await ActivityFeedService.add_event(
            session,
            org_id=org_id,
            user_id=user_id,
            event_type="infrastructure.storage_deployed",
            summary="Storage infrastructure deployed (GCS buckets, Pub/Sub)",
            entity_type="infrastructure",
            entity_id=0,
            metadata={"module": "storage"},
        )
    await session.flush()


async def _after_image_build_apply(session: AsyncSession, user_id: int, outputs: dict) -> None:
    await persist_outputs(session, outputs, IMAGE_BUILD_OUTPUTS, BlankOutputs.WRITE_NULL)
    await _set_config(session, "aws_image_build_deployed", "true")
    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure",
        entity_id=0,
        action="deploy_image_build",
        details={"module": "image_build", "status": "completed"},
    )
    await session.flush()


async def _after_compute_apply(
    session: AsyncSession,
    user_id: int,
    org_id: int | None,
    cloud_provider: str,
    outputs: dict,
) -> None:
    await _set_config(session, "compute_stack", "kubernetes")
    await _set_config(session, "compute_deployed", "true")
    await persist_outputs(session, outputs, COMPUTE_OUTPUTS, BlankOutputs.WRITE_NULL)
    if cloud_provider == "aws":
        await persist_outputs(session, outputs, COMPUTE_AWS_OUTPUTS, BlankOutputs.WRITE_NULL)
    else:
        await persist_outputs(session, outputs, COMPUTE_GCP_OUTPUTS, BlankOutputs.WRITE_NULL)

    # Update kubernetes_cluster component state
    await session.execute(
        text("""
        UPDATE component_states
        SET enabled = true, status = 'running'
        WHERE component_key = 'kubernetes_cluster'
        """)
    )

    # Compute is now usable; flip any cluster-only queued components
    # (nextflow, snakemake, qc_dashboard, meilisearch) to enabled, and
    # flip image-bound components whose images are already built.
    await process_queued_components(session)
    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure",
        entity_id=0,
        action="deploy_compute",
        details={"module": "compute", "stack_type": "kubernetes", "status": "completed"},
    )
    if org_id is not None:
        await ActivityFeedService.add_event(
            session,
            org_id=org_id,
            user_id=user_id,
            event_type="infrastructure.compute_deployed",
            summary="Kubernetes cluster and node pools deployed",
            entity_type="infrastructure",
            entity_id=0,
            metadata={"module": "compute", "stack_type": "kubernetes"},
        )
    await session.flush()


async def _record_failed_compute_orphans(session: AsyncSession, attempted_location: str) -> None:
    """Log the expected cluster and its service accounts as orphaned, where the attempt built it."""
    project_id = await _read_config(session, "gcp_project_id")
    region = attempted_location if is_gke_location(attempted_location) else "us-central1"
    org_slug = await _read_config(session, "org_slug")
    suffix = await _read_config(session, "deploy_suffix")
    if suffix and suffix != "null" and org_slug and org_slug != "null":
        cluster_name = f"bioaf-{org_slug}-{suffix}"
        pid = project_id if project_id != "null" else ""
        await OrphanedResourceService.log_resource(
            session,
            resource_type="gke_cluster",
            resource_name=cluster_name,
            gcp_project_id=pid,
            gcp_zone=region,
            stack_uid=suffix,
        )
        # The compute module also creates a service account
        await OrphanedResourceService.log_resource(
            session,
            resource_type="service_account",
            resource_name="bioaf-notebook-runner",
            gcp_project_id=pid,
            stack_uid=suffix,
        )
        await session.flush()


async def deploy_stack(
    session: AsyncSession,
    stack_type: str,
    user_id: int,
    org_id: int | None = None,
    compute_region: str | None = None,
    compute_zone: str | None = None,
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Deploy a full compute stack (storage + compute).

    Validates pre-conditions, runs storage (if needed), then compute.
    Yields progress events throughout.

    When *compute_region* or *compute_zone* are provided, the compute
    module uses those values instead of the defaults from platform_config.
    Storage always uses the default region.
    """
    # Validate pre-conditions (cloud-aware: GCP credentials flag vs AWS account
    # identity, resolved through the TerraformCloud seam).
    from app.platform.cloud_provider import get_cloud_provider
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.terraform_cloud import get_terraform_cloud

    cloud_provider = await get_cloud_provider(session)
    cloud = get_terraform_cloud(cloud_provider)
    cloud_config = await PlatformConfigService.get_many(session, cloud.config_keys())
    if not cloud.is_configured(cloud_config):
        raise ValidationError(cloud.not_configured_message())

    tf_initialized = await _read_config(session, "terraform_initialized")
    if tf_initialized != "true":
        raise ValidationError("Terraform has not been initialized")

    compute_deployed = await _read_config(session, "compute_deployed")
    if compute_deployed == "true":
        raise ConflictError("Compute stack is already deployed. Teardown first.")

    if stack_type != "kubernetes":
        raise ValidationError(f"Unsupported stack type: {stack_type}")

    storage_deployed = await _read_config(session, "storage_deployed")
    progress = StackProgress()
    # The definitions this deploy applies, captured before anything runs (M17 baselines).
    from app.services import infrastructure_definitions as definitions
    from app.services.infrastructure_operations import current_operation_id

    captured = await definitions.capture(session)

    # Storage is named by its storage generation, persisted before Terraform so
    # a retry resumes the same resources and a deploy after a confirmed destroy
    # gets new names. Compute still takes a fresh deploy suffix per module.
    if storage_deployed != "true":
        await ensure_storage_generation(session)
        await session.commit()

    # Step 1: Deploy storage if needed
    if storage_deployed != "true":
        await _set_config(session, "deploy_suffix", secrets.token_hex(3))
        await session.flush()
        yield TerraformProgressEvent(
            event_type="progress",
            message="Deploying storage infrastructure...",
        )
        storage = _PhaseRun(progress)
        async with aclosing(
            _deploy_phase(
                session,
                user_id,
                "storage",
                storage,
                tag_deploy_phase=True,
                complete_message="Storage deployment complete",
                after_apply=partial(_after_storage_apply, session, user_id, org_id),
            )
        ) as events:
            async for event in events:
                yield event
        progress = storage.progress

        if storage.outcome.failed:
            yield TerraformProgressEvent(
                event_type="stack_error",
                message="Stack deployment failed during storage module",
            )
            return
        if not storage.outcome.succeeded:
            yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
            return
        await definitions.record_reconciled(session, captured, "storage", operation_id=current_operation_id())

    # Step 1b (AWS only): deploy the image-build infrastructure (CodeBuild project
    # + its IAM service role). Cloud Build is serverless on GCP, so GCP has no
    # analog and this step never runs there (it is byte-identical on GCP). It needs
    # only storage (deployed above), and unblocks the notebook + cellxgene image
    # builds. Idempotent: skipped once aws_codebuild_project is recorded (cleared on
    # teardown), so a compute redeploy does not re-run it.
    if cloud_provider == "aws":
        already = await _read_config(session, "aws_codebuild_project")
        if not already or already == "null":
            yield TerraformProgressEvent(
                event_type="progress",
                message="Deploying image build infrastructure...",
            )
            image_build = _PhaseRun(progress)
            async with aclosing(
                _deploy_phase(
                    session,
                    user_id,
                    "image_build",
                    image_build,
                    tag_deploy_phase=False,
                    complete_message="Image build infrastructure deployed",
                    after_apply=partial(_after_image_build_apply, session, user_id),
                )
            ) as events:
                async for event in events:
                    yield event
            progress = image_build.progress

            if image_build.outcome.failed:
                yield TerraformProgressEvent(
                    event_type="stack_error",
                    message="Stack deployment failed during image build module",
                )
                return
            if not image_build.outcome.succeeded:
                yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
                return
            await definitions.record_reconciled(session, captured, "image_build", operation_id=current_operation_id())

    # Step 2: Deploy compute
    # If the user chose a different region/zone for compute, temporarily
    # override the config so _write_tfvars picks up the override values.
    # Restore defaults after deploy completes (success or failure).
    original_region = None
    original_zone = None
    # The region this attempt deploys the cluster in (the module's region variable), kept so a
    # failed attempt's orphan is recorded where it was built, not at the restored default.
    attempted_location = ""
    compute = _PhaseRun(progress)
    interrupted = False
    try:
        # The region/zone override writes gcp_region/gcp_zone and is GCP-specific (the
        # GKE module reads them). On AWS the compute module uses the install's
        # aws_region; a per-deploy region override is not wired yet, so skip this.
        if cloud_provider == "gcp":
            if compute_region:
                original_region = await _read_config(session, "gcp_region")
                await _set_config(session, "gcp_region", compute_region)
                if not compute_zone:
                    from app.gcp_zones import default_zone

                    compute_zone = default_zone(compute_region)
            if compute_zone:
                original_zone = await _read_config(session, "gcp_zone")
                await _set_config(session, "gcp_zone", compute_zone)
            attempted_location = await _read_config(session, "gcp_region")

        new_suffix = secrets.token_hex(3)
        await _set_config(session, "deploy_suffix", new_suffix)
        # On AWS, pin a stable compute_stack_uid so a retry after a failed deploy
        # RESUMES the same-named resources instead of destroying + recreating them
        # under a fresh suffix (which is what bit a failed EKS deploy: the retry's new
        # suffix made terraform plan a full destroy of the half-built cluster). The
        # storage path already pins storage_stack_uid for the same reason. This is an
        # AWS-only divergence on purpose: GCP intentionally regenerates the suffix each
        # deploy to dodge soft-deleted-resource name collisions on redeploy (GCS/IAM
        # soft-delete for ~30d), whereas AWS names are immediately reusable, so pinning
        # is both safe and correct there. GCP is untouched (it never sets
        # compute_stack_uid, so it keeps falling back to the regenerated deploy_suffix).
        if cloud_provider == "aws":
            existing_compute_uid = await _read_config(session, "compute_stack_uid")
            if not existing_compute_uid or existing_compute_uid == "null":
                await _set_config(session, "compute_stack_uid", new_suffix)
        await session.flush()

        # Pre-flight: probe regional zones for e2-medium capacity. The GKE
        # default node pool that gets created (and immediately removed) at
        # cluster bootstrap has no autoscaling and no location_policy, so a
        # per-zone stockout hangs CREATE_CLUSTER for ~70 minutes. By picking
        # one healthy zone here and pinning the cluster's node_locations to
        # it, we contain the failure surface to a single zone we have just
        # observed to have capacity. The real node pools override
        # node_locations per-pool, so the cluster stays regional.
        #
        # This is GCP-specific (GCE capacity + the GKE default-pool failure mode).
        # The EKS module places node groups across two AZs with no throwaway default
        # pool, so AWS skips the probe entirely.
        if cloud_provider == "gcp":
            yield TerraformProgressEvent(
                event_type="progress",
                message="Checking GCE zone capacity for cluster bootstrap...",
            )
            try:
                selected_zone = await _select_default_pool_zone(session, compute_region)
            except AllZonesExhaustedError as exc:
                logger.warning("Zone capacity probe found no available zone: %s", exc)
                yield TerraformProgressEvent(
                    event_type="stack_error",
                    message=(
                        "GCE capacity probe found no zone in the region with capacity for "
                        "cluster bootstrap. Wait a few minutes for capacity to free up and "
                        f"redeploy. Details: {exc}"
                    ),
                )
                return

            await _set_config(session, "gke_default_pool_zone", selected_zone)
            await session.flush()
            yield TerraformProgressEvent(
                event_type="progress",
                message=f"Selected zone {selected_zone} for cluster bootstrap (has capacity).",
            )

        yield TerraformProgressEvent(
            event_type="progress",
            message="Deploying compute infrastructure...",
        )
        async with aclosing(
            _deploy_phase(
                session,
                user_id,
                "compute",
                compute,
                tag_deploy_phase=True,
                complete_message="Compute deployment complete",
                after_apply=partial(_after_compute_apply, session, user_id, org_id, cloud_provider),
            )
        ) as events:
            async for event in events:
                yield event
    except asyncio.CancelledError:
        logger.warning("Compute deploy interrupted")
        interrupted = True
    finally:
        # Restore on every exit, including cancellation and closure. This is a
        # flush: an owner that rolls back an unexpected error discards it, and an
        # override an earlier commit persisted then stays (deferred D2 work).
        await _restore_compute_location(session, original_region, original_zone)

    progress = compute.progress

    if interrupted or not (compute.outcome.succeeded or compute.outcome.failed):
        yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
        return

    if compute.outcome.failed:
        await _record_failed_compute_orphans(session, attempted_location)
        yield TerraformProgressEvent(
            event_type="stack_error",
            message="Stack deployment failed during compute module. Storage buckets preserved.",
        )
        return

    # AWS post-compute: install the in-cluster Cluster Autoscaler. EKS managed
    # node groups do NOT pod-autoscale natively (GKE's control plane does), so
    # without this a launched pipeline/notebook pod that targets a scaled-to-zero
    # pool sits Pending forever and nothing scales a node up. Best-effort: the
    # cluster IS deployed at this point, so a CA hiccup must not roll it back --
    # surface a clear warning and leave it retryable (the install is idempotent).
    # GCP autoscales node pools natively, so this is skipped there entirely.
    if cloud_provider == "aws":
        yield TerraformProgressEvent(
            event_type="progress",
            message="Installing cluster autoscaler...",
        )
        try:
            ca_status = await _ensure_aws_cluster_autoscaler(session)
            await session.flush()
            yield TerraformProgressEvent(
                event_type="progress",
                message=f"Cluster autoscaler {ca_status}.",
            )
        except Exception as exc:
            logger.exception("Cluster autoscaler install failed")
            yield TerraformProgressEvent(
                event_type="progress",
                message=(
                    f"Warning: cluster autoscaler install failed ({exc}). The cluster is "
                    "deployed, but pipelines and notebooks cannot scale nodes until it is "
                    "installed; re-running the compute deploy will retry it (idempotent)."
                ),
            )

    await definitions.record_reconciled(session, captured, "compute", operation_id=current_operation_id())
    await definitions.refresh_notice(session)
    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Stack deployment complete",
        resources_completed=progress.completed,
        resources_total=progress.planned,
    )


async def _restore_compute_location(
    session: AsyncSession,
    original_region: str | None,
    original_zone: str | None,
) -> None:
    """Put back the default region/zone after a compute override."""
    with anyio.CancelScope(shield=True):
        try:
            if original_region is not None:
                await _set_config(session, "gcp_region", original_region)
            if original_zone is not None:
                await _set_config(session, "gcp_zone", original_zone)
        except Exception:
            logger.exception("Could not restore the default compute location")


async def _ensure_aws_cluster_autoscaler(session: AsyncSession) -> str:
    """Install/refresh the EKS in-cluster Cluster Autoscaler from platform_config.

    Reads the CA's IRSA role ARN (terraform output, captured at compute apply),
    the cluster name, and the region, then delegates the actual kube-system apply
    to the compute adapter (which owns the cluster connection + the k8s SDK).
    Returns a short status word for the progress message. Raises on a real apply
    failure so the caller can surface it; returns ``skipped`` when the role ARN is
    absent (e.g. a cluster deployed before this feature -- nothing to install).
    """
    from app.adapters.registry import get_compute_adapter
    from app.platform.platform_config_service import PlatformConfigService

    cfg = await PlatformConfigService.get_many(
        session,
        ["cluster_autoscaler_role_arn", "gke_cluster_name", "aws_region", "cluster_autoscaler_image"],
    )
    role_arn = cfg.get("cluster_autoscaler_role_arn")
    if not role_arn or role_arn == "null":
        logger.warning("No cluster_autoscaler_role_arn in platform_config; skipping CA install")
        return "skipped (no role ARN output)"

    region = cfg.get("aws_region")
    image = cfg.get("cluster_autoscaler_image")
    await get_compute_adapter().ensure_cluster_autoscaler(
        role_arn=role_arn,
        cluster_name=(cfg.get("gke_cluster_name") or ""),
        region=(region if region and region != "null" else ""),
        image=(image if image and image != "null" else None),
    )
    return "installed"


async def teardown_stack(
    session: AsyncSession,
    user_id: int,
    org_id: int | None = None,
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Teardown the compute stack (preserves storage).

    Destroys the compute module and clears GKE config from platform_config.
    """
    compute_deployed = await _read_config(session, "compute_deployed")
    if compute_deployed != "true":
        raise StateError("Compute stack is not deployed")

    yield TerraformProgressEvent(
        event_type="progress",
        message="Tearing down compute infrastructure...",
    )

    outcome = OperationOutcome()
    async with aclosing(_run_destroy(session, user_id, "compute")) as events:
        async for event in events:
            outcome.observe(event)
            if event.event_type != INTERRUPTED_EVENT:
                yield event

    if outcome.failed:
        # Log the cluster and its service accounts as orphaned
        project_id = await _read_config(session, "gcp_project_id")
        region = await _deployed_cluster_location(session)
        cluster_name = await _read_config(session, "gke_cluster_name")
        if cluster_name and cluster_name != "null":
            pid = project_id if project_id != "null" else ""
            uid = cluster_name.rsplit("-", 1)[-1]
            await OrphanedResourceService.log_resource(
                session,
                resource_type="gke_cluster",
                resource_name=cluster_name,
                gcp_project_id=pid,
                gcp_zone=region,
                stack_uid=uid,
            )
            await OrphanedResourceService.log_resource(
                session,
                resource_type="service_account",
                resource_name="bioaf-notebook-runner",
                gcp_project_id=pid,
                stack_uid=uid,
            )
            await session.flush()

        yield TerraformProgressEvent(
            event_type="stack_error",
            message="Teardown failed",
        )
        return

    if not outcome.succeeded:
        # The cluster may or may not still exist, so keep what identifies it.
        yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
        return

    # Clear GKE config
    await _set_config(session, "compute_deployed", "false")
    for key in COMPUTE_TEARDOWN_CLEARED_KEYS:
        await _set_config(session, key, "null")

    # Update kubernetes_cluster component state
    await session.execute(
        text("""
        UPDATE component_states
        SET enabled = false, status = 'disabled'
        WHERE component_key = 'kubernetes_cluster'
        """)
    )

    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure",
        entity_id=0,
        action="teardown_compute",
        details={"module": "compute", "status": "completed"},
    )
    if org_id is not None:
        await ActivityFeedService.add_event(
            session,
            org_id=org_id,
            user_id=user_id,
            event_type="infrastructure.compute_teardown",
            summary="Kubernetes cluster and node pools destroyed",
            entity_type="infrastructure",
            entity_id=0,
            metadata={"module": "compute"},
        )
    from app.services import infrastructure_definitions as definitions

    await definitions.forget(session, "compute")
    await definitions.refresh_notice(session)
    await session.flush()

    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Teardown complete",
    )


class PartialEmptyError(Exception):
    """Emptying a bucket stopped after ``deleted`` objects were confirmed deleted."""

    def __init__(self, deleted: int, cause: BaseException):
        super().__init__(f"emptying stopped after {deleted} objects")
        self.deleted = deleted
        self.cause = cause


async def _empty_gcs_bucket(session: AsyncSession, bucket_name: str) -> int:
    """Delete all objects (including noncurrent versions) from a bucket.

    Returns the number of objects deleted. Routes through the storage adapter,
    which owns credential resolution; ``include_versions`` enumerates every
    generation and each is deleted by its generation id (versioned-wipe).

    Byte loss is recorded as each deletion is confirmed, so a failure part way through keeps an
    accurate record of what is gone; it raises ``PartialEmptyError`` carrying the confirmed count.
    """
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    uri_prefix = adapter.build_uri(bucket_name, "")

    deleted = 0
    try:
        for obj in await adapter.list_objects(uri_prefix, include_versions=True):
            generation = obj.provider_details.get("generation")
            await adapter.delete(obj.storage_uri, generation=generation)
            deleted += 1
            await session.execute(
                text(
                    "UPDATE files SET storage_deleted = true WHERE storage_deleted = false AND storage_uri = :uri"
                ).bindparams(uri=obj.storage_uri)
            )
    except Exception as exc:
        await session.flush()
        raise PartialEmptyError(deleted, exc) from exc
    await session.flush()
    return deleted


def _step(key: str, label: str, state: str, detail: str | None = None) -> TerraformProgressEvent:
    return TerraformProgressEvent(
        event_type="step", message=label, extra={"key": key, "label": label, "state": state, "detail": detail}
    )


def _stopped(message: str, *, category: str, step_detail: str | None = None, **effects) -> TerraformProgressEvent:
    extra: dict = {"category": category, "effects": effects}
    if step_detail is not None:
        extra["step_detail"] = step_detail
    return TerraformProgressEvent(event_type="stack_error", message=message, extra=extra)


_RESUME = "It continues from where it stopped. Uploads remain paused until you run it again or choose Allow uploads."
_EMPTY_ATTEMPTS = 3


async def _provider_name(session: AsyncSession) -> str:
    from app.platform.cloud_provider import get_cloud_provider

    return "AWS" if await get_cloud_provider(session) == "aws" else "Google Cloud"


async def _mark_bucket_deleted(session: AsyncSession, bucket_name: str) -> int:
    """Record byte loss for the files stored in one emptied or absent bucket. Metadata stays."""
    from app.adapters.registry import get_storage_adapter

    prefix = get_storage_adapter().build_uri(bucket_name, "")
    result = await session.execute(
        text(
            "UPDATE files SET storage_deleted = true WHERE storage_deleted = false AND storage_uri LIKE :prefix"
        ).bindparams(prefix=prefix.replace("%", r"\%").replace("_", r"\_") + "%")
    )
    await session.flush()
    return result.rowcount or 0


_ACTIVITIES = {
    "thumbnail": "Saving a plot thumbnail",
    "QC metrics cache": "Saving QC results",
    "data export": "Creating a data export",
    "environment build context": "Preparing an environment build",
    "environment definition": "Preparing an environment build",
    "reference import": "Importing reference data",
}


def _activities(writers) -> list[dict]:
    """bioAF's own background work still writing, by activity (W6)."""
    return [
        {"activity": _ACTIVITIES.get(w.filename, "Saving files"), "started_at": w.started_at.isoformat()}
        for w in sorted(writers, key=lambda w: w.started_at)
    ]


def _retry_after(writers) -> str | None:
    """When every blocking upload is expected to have settled, if bioAF can say.

    Only when each one has a known settlement time: an upload still sending finishes when it
    finishes, and one bioAF lost track of needs an administrator.
    """
    times = [w.settles_after for w in writers]
    if not times or any(t is None for t in times):
        return None
    return max(times).isoformat()


async def _unconfirmed_writers(session: AsyncSession, writers) -> list[dict]:
    """Uploads bioAF could not settle with the provider: an administrator may release them."""
    from sqlalchemy import select

    from app.models.user import User

    unconfirmed = [w for w in writers if w.needs_release]
    user_ids = {w.user_id for w in unconfirmed if w.user_id is not None}
    names = (
        dict((await session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    )
    return [
        {
            "upload_id": w.upload_id,
            "reference": w.reference,
            "filename": w.filename,
            "user_id": w.user_id,
            "name": names.get(w.user_id),
            "started_at": w.started_at.isoformat(),
        }
        for w in unconfirmed
    ]


async def _writers_summary(session: AsyncSession, writers) -> list[dict]:
    """M11's list: who is uploading, how many files, into which experiment, since when."""
    from sqlalchemy import select

    from app.models.experiment import Experiment
    from app.models.user import User

    groups: dict[tuple, dict] = {}
    for w in writers:
        key = (w.user_id, w.experiment_id)
        group = groups.setdefault(
            key,
            {
                "user_id": w.user_id,
                "experiment_id": w.experiment_id,
                "files": 0,
                "started_at": w.started_at,
            },
        )
        group["files"] += 1
        group["started_at"] = min(group["started_at"], w.started_at)
    user_ids = {g["user_id"] for g in groups.values() if g["user_id"] is not None}
    exp_ids = {g["experiment_id"] for g in groups.values() if g["experiment_id"] is not None}
    names = (
        dict((await session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    )
    experiments = (
        dict((await session.execute(select(Experiment.id, Experiment.name).where(Experiment.id.in_(exp_ids)))).all())
        if exp_ids
        else {}
    )
    return [
        {
            **g,
            "name": names.get(g["user_id"]),
            "experiment": experiments.get(g["experiment_id"]),
            "started_at": g["started_at"].isoformat(),
        }
        for g in groups.values()
    ]


async def destroy_storage(
    session: AsyncSession,
    user_id: int,
    org_id: int | None = None,
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Destroy the storage module (buckets + Pub/Sub) safely and retryably (D3, D5; mockups M7, M8, M11).

    Before deleting anything: every configured bucket must be this installation's (or confirmed
    absent), new uploads are paused, and running uploads get 30 seconds to finish; any that remain
    or cannot be confirmed stop the destroy with nothing deleted. A bucket the provider confirms does
    not exist counts as already removed. A refused or failing bucket stops the destroy with what was
    done recorded; running it again continues from there. Only a confirmed destroy clears the storage
    configuration, retires the storage generation and reopens uploads.
    """
    from app import database
    from app.services import storage_identity
    from app.services.infrastructure_operations import current_operation_id
    from app.services.storage_identity import StoreHealth
    from app.services.storage_write_admission import drain, pause_writes

    compute_deployed = await _read_config(session, "compute_deployed")
    if compute_deployed == "true":
        raise StateError("Cannot destroy storage while compute stack is deployed. Teardown compute first.")

    storage_deployed = await _read_config(session, "storage_deployed")
    if storage_deployed != "true":
        raise StateError("Storage is not deployed.")

    provider = await _provider_name(session)
    buckets: list[tuple[str, str]] = []
    for config_key in STORAGE_BUCKETS_TO_EMPTY:
        name = await _read_config(session, config_key)
        if name and name != "null":
            buckets.append((config_key.removesuffix("_bucket_name"), name))

    # Step 1: identity. Destruction acts only on buckets established as this installation's.
    reports = {name: await storage_identity.inspect_bucket(session, store, name) for store, name in buckets}
    await session.commit()
    foreign = [n for n, r in reports.items() if r.health is StoreHealth.WRONG_OWNER]
    unconfirmed = [n for n, r in reports.items() if r.health is StoreHealth.NOT_CONFIRMED]
    if foreign:
        yield _stopped(
            f"A bucket with this name exists but belongs to another installation: {foreign[0]}. "
            "bioAF will not use, empty, or delete it. Nothing was changed.",
            category="wrong_owner",
        )
        return
    if unconfirmed:
        yield _stopped(
            "Storage destroy is unavailable until an administrator confirms the storage buckets. Nothing was changed.",
            category="not_confirmed",
        )
        return

    # Step 2: pause new uploads, then give running ones 30 seconds to finish.
    yield _step("pause_uploads", "Pausing uploads", "running")
    await pause_writes(session, current_operation_id())
    await session.commit()
    yield _step("pause_uploads", "Pausing uploads", "done")
    yield _step("drain_uploads", "Waiting for uploads to finish", "running")
    remaining = await drain(database.async_session_factory)
    if remaining:
        # Uploads are counted; any whose outcome is still being settled is uncertain. bioAF's own
        # background work is named by activity (W6).
        uploads = [w for w in remaining if w.kind != "background"]
        own_work = [w for w in remaining if w.kind == "background"]
        uncertain = any(w.kind == "signed" or w.state != "sending" for w in uploads)
        if own_work and not uploads:
            detail = (
                "bioAF is still saving files. Nothing was deleted. "
                "Try Destroy Storage again when this work has finished."
            )
        elif own_work:
            detail = "Files are still being uploaded or saved. Nothing was deleted."
        elif uncertain:
            detail = "bioAF could not confirm that every upload had finished, so nothing was deleted."
        else:
            detail = (
                f"{len(uploads)} upload{'s were' if len(uploads) != 1 else ' was'} still in progress "
                "after 30 seconds, so nothing was deleted."
            )
        yield _stopped(
            f"{detail} New uploads stay paused until you allow them or run Destroy Storage again.",
            category="uploads_in_progress",
            step_detail=detail,
            uploads=await _writers_summary(session, uploads),
            activities=_activities(own_work),
            retry_after=_retry_after(remaining),
            unconfirmed=await _unconfirmed_writers(session, remaining),
        )
        return
    yield _step("drain_uploads", "Waiting for uploads to finish", "done")

    # Step 3: empty each bucket (buckets have force_destroy=false), recording byte loss as it is confirmed.
    emptied = 0
    removed_outside = 0
    deleted_objects = 0
    for store, bucket_name in buckets:
        key, label = f"empty:{bucket_name}", f"Emptying {bucket_name}"
        report = reports[bucket_name]
        if report.health is StoreHealth.NOT_FOUND:
            yield _step(key, bucket_name, "already_removed", f"{provider} reports this bucket does not exist.")
            await _mark_bucket_deleted(session, bucket_name)
            await session.commit()
            removed_outside += 1
            continue
        yield _step(key, label, "running")
        # Objects are deleted only while the bucket's identity is established (AVAILABLE). An
        # unresponsive provider is checked again after a pause; any other answer stops here.
        count, failure, health = 0, None, report.health
        for attempt in range(1, _EMPTY_ATTEMPTS + 1):
            if health is StoreHealth.AVAILABLE:
                try:
                    count += await _empty_gcs_bucket(session, bucket_name)
                    failure = None
                    break
                except Exception as exc:
                    count += exc.deleted if isinstance(exc, PartialEmptyError) else 0
                    cause = exc.cause if isinstance(exc, PartialEmptyError) else exc
                    # The provider's error text can carry signed or internal URLs: log its type only.
                    logger.warning(
                        "Emptying bucket %s failed (attempt=%d, error_type=%s)",
                        bucket_name,
                        attempt,
                        type(cause).__name__,
                    )
                    await session.commit()
                    health = (await storage_identity.inspect_bucket(session, store, bucket_name)).health
            failure = health
            if health is not StoreHealth.NOT_RESPONDING or attempt == _EMPTY_ATTEMPTS:
                break
            await asyncio.sleep(min(2**attempt, 10))
            health = (await storage_identity.inspect_bucket(session, store, bucket_name)).health
        if failure is StoreHealth.NOT_FOUND:
            yield _step(key, bucket_name, "already_removed", f"{provider} reports this bucket does not exist.")
            await _mark_bucket_deleted(session, bucket_name)
            await session.commit()
            removed_outside += 1
            continue
        if failure is not None:
            nothing = "Nothing was deleted from it." if count == 0 else "Nothing more was deleted from it."
            if failure is StoreHealth.NO_ACCESS:
                cause = f"bioAF is not allowed to read this bucket. {nothing}"
                fix = f"Give the bioAF service account access to {bucket_name} in {provider}, then run Destroy Storage again."
                category = "no_access"
            elif failure is StoreHealth.NOT_RESPONDING or failure is StoreHealth.AVAILABLE:
                cause = f"{provider} did not respond after several tries. Nothing more was deleted from it."
                fix = "Run Destroy Storage again when it responds."
                category = "provider_unavailable"
            else:
                cause = f"bioAF could not confirm this bucket belongs to this installation. {nothing}"
                fix = "Confirm the bucket under Infrastructure > Components, then run Destroy Storage again."
                category = "not_confirmed"
            # Byte loss for the objects confirmed deleted was recorded as each one went.
            await session.commit()
            yield _stopped(
                f"Nothing else was changed. {fix} {_RESUME}",
                category=category,
                step_detail=cause,
                emptied=emptied,
                bucket=bucket_name,
            )
            return
        deleted_objects += count
        logger.info("Emptied bucket %s (%d objects deleted)", bucket_name, count)
        await _mark_bucket_deleted(session, bucket_name)
        await session.commit()
        emptied += 1
        yield _step(key, label, "done", f"{count} object{'s' if count != 1 else ''} deleted")

    # Step 3b (AWS only): destroy the image-build infrastructure (CodeBuild project
    # + IAM role) before storage. It is install-level like storage, so a compute
    # teardown intentionally leaves it intact (the operator's teardown/redeploy
    # cycle is compute-only); it is removed only on this full storage teardown. GCP
    # has no such module, so this is skipped there.
    from app.platform.cloud_provider import get_cloud_provider

    cloud_provider = await get_cloud_provider(session)
    if cloud_provider == "aws" and await _read_config(session, "aws_image_build_deployed") == "true":
        yield TerraformProgressEvent(
            event_type="progress",
            message="Destroying image build infrastructure...",
        )
        image_build_outcome = OperationOutcome()
        async with aclosing(_run_destroy(session, user_id, "image_build")) as events:
            async for event in events:
                image_build_outcome.observe(event)
                if event.event_type != INTERRUPTED_EVENT:
                    yield event
        if image_build_outcome.failed:
            yield TerraformProgressEvent(
                event_type="stack_error",
                message="Image build destroy failed",
            )
            return
        if not image_build_outcome.succeeded:
            yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
            return
        await _set_config(session, "aws_codebuild_project", "null")
        await _set_config(session, "aws_image_build_deployed", "false")
        await session.flush()

    # Step 4: Run terraform destroy on the now-empty buckets
    yield TerraformProgressEvent(
        event_type="progress",
        message="Destroying storage infrastructure...",
    )

    outcome = OperationOutcome()
    async with aclosing(_run_destroy(session, user_id, "storage")) as events:
        async for event in events:
            outcome.observe(event)
            if event.event_type != INTERRUPTED_EVENT:
                yield event

    if outcome.failed:
        yield TerraformProgressEvent(
            event_type="stack_error",
            message="Storage destroy failed",
        )
        return

    if not outcome.succeeded:
        yield TerraformProgressEvent(event_type=INTERRUPTED_EVENT)
        return

    # Clear all storage-related resource names from platform_config, retire
    # the storage generation (the next deploy allocates a new one), and reopen
    # uploads: the pause belonged to this destroy.
    from app.services.storage_write_admission import allow_writes

    for key in STORAGE_DESTROY_CLEARED_KEYS:
        await _set_config(session, key, "null")
    await retire_storage_generation(session)
    await allow_writes(session)
    from app.services import infrastructure_definitions as definitions

    await definitions.forget(session, "storage")
    await definitions.forget(session, "image_build")
    await definitions.refresh_notice(session)

    await log_action(
        session,
        user_id=user_id,
        entity_type="infrastructure",
        entity_id=0,
        action="destroy_storage",
        details={"module": "storage", "status": "completed"},
    )
    if org_id is not None:
        await ActivityFeedService.add_event(
            session,
            org_id=org_id,
            user_id=user_id,
            event_type="infrastructure.storage_destroyed",
            summary="Storage infrastructure destroyed (GCS buckets, Pub/Sub)",
            entity_type="infrastructure",
            entity_id=0,
            metadata={"module": "storage"},
        )
    await session.flush()

    total = emptied + removed_outside
    summary = f"Storage destroyed. {total} bucket{'s' if total != 1 else ''} emptied and removed."
    if removed_outside:
        summary += f" {removed_outside} had already been removed outside bioAF."
    summary += " A new deployment will create new buckets with new names."
    yield TerraformProgressEvent(
        event_type="stack_complete",
        message=summary,
        extra={"effects": {"emptied": emptied, "removed_outside": removed_outside, "deleted_objects": deleted_objects}},
    )


async def sync_storage_config(session: AsyncSession) -> dict[str, str]:
    """Re-read the storage Terraform outputs and write bucket names to platform_config.

    Used to recover deployments where storage was applied before the output-
    persistence fix was in place.  Returns a dict of {config_key: bucket_name}
    for all keys that were successfully populated.
    """
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.deployment_outputs import identifier_value
    from app.services.storage_identity import inspect_bucket

    outputs = await TerraformExecutor.read_module_outputs(session, "storage")
    populated: dict[str, str] = {}
    for output_name, config_key in STORAGE_SYNC_OUTPUTS.items():
        value = identifier_value(str(outputs.get(output_name, {}).get("value") or ""))
        if not value:
            continue
        recorded = identifier_value(await PlatformConfigService.get(session, config_key))
        if recorded == value:
            continue
        if recorded:
            # A Terraform output never replaces a recorded bucket: that would move
            # where data is written on the strength of state alone.
            logger.warning("Storage sync left %s unchanged: Terraform output names a different bucket", config_key)
            continue
        report = await inspect_bucket(session, config_key.removesuffix("_bucket_name"), value)
        if not report.trusted:
            logger.warning("Storage sync did not record %s: bucket health is %s", config_key, report.health.value)
            continue
        await PlatformConfigService.set(session, config_key, value)
        populated[config_key] = value
    await session.flush()
    return populated


async def sync_compute_config(session: AsyncSession) -> dict[str, str]:
    """Re-read the compute Terraform outputs and write cluster config to platform_config.

    Used to recover deployments where the terraform output capture failed
    silently, leaving gke_cluster_endpoint as 'null'. Returns a dict of
    {config_key: value} for all keys that were successfully populated.
    """
    from app.platform.cloud_provider import get_cloud_provider

    outputs = await TerraformExecutor.read_module_outputs(session, "compute")
    populated = await persist_outputs(session, outputs, COMPUTE_OUTPUTS, BlankOutputs.SKIP)
    if await get_cloud_provider(session) == "aws":
        populated |= await persist_outputs(session, outputs, COMPUTE_AWS_OUTPUTS, BlankOutputs.SKIP)
    else:
        populated |= await persist_outputs(session, outputs, COMPUTE_GCP_OUTPUTS, BlankOutputs.SKIP)
    await session.flush()
    return populated
