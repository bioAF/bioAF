"""Phase 19 - Stack deployment service.

Orchestrates full stack deployment (storage + compute) and teardown.
Provides cluster status via GKE API.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import AsyncGenerator

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, StateError, ValidationError
from app.services.activity_feed_service import ActivityFeedService
from app.services.audit_service import log_action
from app.services.component_queue import process_queued_components
from app.services.orphaned_resource_service import OrphanedResourceService
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent
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
        logger.error("Failed to query GKE cluster status: %s", exc)
        return StackStatus(
            compute_stack=stack,
            compute_deployed=True,
            storage_deployed=storage,
            pubsub_configured=pubsub,
            cluster=None,
            has_orphaned_clusters=has_orphans,
        )


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

    This is the real implementation. Tests mock this function.
    """
    try:
        run = await TerraformExecutor.run_plan(session, user_id, module_name=module_name)
    except asyncio.CancelledError:
        # Connection dropped during plan -- mark any active run as failed
        logger.warning("Plan cancelled for module %s (client disconnected)", module_name)
        await session.execute(
            text("""
            UPDATE terraform_runs
            SET status = 'failed',
                error_message = 'Operation cancelled (client disconnected)',
                completed_at = now()
            WHERE status IN ('planning', 'applying')
              AND module_name = :mod
            """).bindparams(mod=module_name)
        )
        await session.commit()
        return
    await session.commit()

    if run.status != "awaiting_confirmation":
        yield TerraformProgressEvent(
            event_type="apply_error",
            message=run.error_message or f"Plan failed for {module_name}",
        )
        return

    async for event in TerraformExecutor.run_apply(session, run.id, user_id):
        yield event


async def _run_destroy(
    session: AsyncSession, user_id: int, module_name: str
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Run destroy for a Terraform module. Tests mock this function."""
    async for event in TerraformExecutor.run_destroy(session, user_id, module_name):
        yield event


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
    storage_failed = False
    compute_failed = False
    # Track per-phase counts to build an accurate cumulative progress bar.
    storage_completed = 0
    storage_planned = 0
    compute_completed = 0
    compute_planned = 0

    # Generate a fresh deploy suffix for each module. This short hex
    # string is appended to GCP resource names so that redeploys after
    # a teardown get new names (avoids GCP's 7-day soft-delete window).
    # The suffix is set before each module runs and cleared after.

    # Step 1: Deploy storage if needed
    if storage_deployed != "true":
        await _set_config(session, "deploy_suffix", secrets.token_hex(3))
        await session.flush()
        yield TerraformProgressEvent(
            event_type="progress",
            message="Deploying storage infrastructure...",
        )
        storage_phase_tagged = False
        async for event in _run_module(session, user_id, "storage"):
            if not storage_phase_tagged:
                await session.execute(
                    text("""
                    UPDATE terraform_runs SET deploy_phase = 'storage'
                    WHERE module_name = 'storage'
                      AND status IN ('planning', 'applying', 'awaiting_confirmation')
                    """)
                )
                await session.flush()
                storage_phase_tagged = True
            if event.event_type == "apply_error":
                storage_failed = True
                yield event
            elif event.event_type == "apply_complete":
                # Storage post-apply hook -- remap to phase_complete so
                # the frontend does not treat this as the final event.
                outputs = event.extra.get("outputs", {})
                for config_key in [
                    "ingest_bucket_name",
                    "raw_bucket_name",
                    "working_bucket_name",
                    "results_bucket_name",
                    "references_bucket_name",
                    "literature_bucket_name",
                    "config_backups_bucket_name",
                    "pubsub_topic_name",
                    "pubsub_subscription_name",
                ]:
                    output_val = outputs.get(config_key, {}).get("value", "")
                    if output_val:
                        await _set_config(session, config_key, output_val)
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
                storage_completed = event.resources_completed
                storage_planned = event.resources_total
                yield TerraformProgressEvent(
                    event_type="phase_complete",
                    message="Storage deployment complete",
                    resources_completed=storage_completed,
                    resources_total=storage_planned,
                )
            else:
                yield event

        if storage_failed:
            yield TerraformProgressEvent(
                event_type="stack_error",
                message="Stack deployment failed during storage module",
            )
            return

    # Step 1b (AWS only): deploy the image-build infrastructure (CodeBuild project
    # + its IAM service role). Cloud Build is serverless on GCP, so GCP has no
    # analog and this step never runs there (it is byte-identical on GCP). It needs
    # only storage (deployed above), and unblocks the notebook + cellxgene image
    # builds. Idempotent: skipped once aws_codebuild_project is recorded (cleared on
    # teardown), so a compute redeploy does not re-run it.
    if cloud_provider == "aws":
        already = await _read_config(session, "aws_codebuild_project")
        if not already or already == "null":
            image_build_failed = False
            yield TerraformProgressEvent(
                event_type="progress",
                message="Deploying image build infrastructure...",
            )
            async for event in _run_module(session, user_id, "image_build"):
                if event.event_type == "apply_error":
                    image_build_failed = True
                    yield event
                elif event.event_type == "apply_complete":
                    outputs = event.extra.get("outputs", {})
                    project = outputs.get("codebuild_project_name", {}).get("value", "")
                    await _set_config(session, "aws_codebuild_project", project or "null")
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
                    yield TerraformProgressEvent(
                        event_type="phase_complete",
                        message="Image build infrastructure deployed",
                        resources_completed=event.resources_completed,
                        resources_total=event.resources_total,
                    )
                else:
                    yield event

            if image_build_failed:
                yield TerraformProgressEvent(
                    event_type="stack_error",
                    message="Stack deployment failed during image build module",
                )
                return

    # Step 2: Deploy compute
    # If the user chose a different region/zone for compute, temporarily
    # override the config so _write_tfvars picks up the override values.
    # Restore defaults after deploy completes (success or failure).
    original_region = None
    original_zone = None
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
    compute_phase_tagged = False
    async for event in _run_module(session, user_id, "compute"):
        if not compute_phase_tagged:
            await session.execute(
                text("""
                UPDATE terraform_runs SET deploy_phase = 'compute'
                WHERE module_name = 'compute'
                  AND status IN ('planning', 'applying', 'awaiting_confirmation')
                """)
            )
            await session.flush()
            compute_phase_tagged = True
        if event.event_type == "apply_error":
            compute_failed = True
            yield event
        elif event.event_type == "apply_complete":
            # Compute post-apply hook: store cluster config.
            # Remap to phase_complete -- stack_complete is yielded below.
            outputs = event.extra.get("outputs", {})
            cluster_name = outputs.get("cluster_name", {}).get("value", "")
            cluster_endpoint = outputs.get("cluster_endpoint", {}).get("value", "")
            cluster_ca_cert = outputs.get("cluster_ca_cert", {}).get("value", "")
            notebook_runner_sa = outputs.get("notebook_runner_sa_email", {}).get("value", "")
            cellxgene_runner_sa = outputs.get("cellxgene_runner_sa_email", {}).get("value", "")

            await _set_config(session, "compute_stack", "kubernetes")
            await _set_config(session, "compute_deployed", "true")
            await _set_config(session, "gke_cluster_name", cluster_name or "null")
            await _set_config(session, "gke_cluster_endpoint", cluster_endpoint or "null")
            await _set_config(session, "gke_cluster_ca_cert", cluster_ca_cert or "null")
            await _set_config(session, "notebook_runner_sa_email", notebook_runner_sa or "null")
            await _set_config(session, "cellxgene_runner_sa_email", cellxgene_runner_sa or "null")

            # AWS (EKS) outputs the same cluster_* keys (reused above), plus the
            # IRSA role ARNs + OIDC issuer the AWS pod-identity / cluster-auth
            # seams need. These keys are additive and never written on GCP.
            if cloud_provider == "aws":
                pipeline_runner_arn = outputs.get("pipeline_runner_role_arn", {}).get("value", "")
                oidc_provider_arn = outputs.get("oidc_provider_arn", {}).get("value", "")
                oidc_provider_url = outputs.get("oidc_provider_url", {}).get("value", "")
                cluster_autoscaler_arn = outputs.get("cluster_autoscaler_role_arn", {}).get("value", "")
                await _set_config(session, "pipeline_runner_role_arn", pipeline_runner_arn or "null")
                await _set_config(session, "notebook_runner_role_arn", notebook_runner_sa or "null")
                await _set_config(session, "cellxgene_runner_role_arn", cellxgene_runner_sa or "null")
                await _set_config(session, "eks_oidc_provider_arn", oidc_provider_arn or "null")
                await _set_config(session, "eks_oidc_provider_url", oidc_provider_url or "null")
                # IRSA role for the in-cluster Cluster Autoscaler; the CA workload
                # is installed below (post-compute) using this ARN.
                await _set_config(session, "cluster_autoscaler_role_arn", cluster_autoscaler_arn or "null")
                # Work-node (EC2) networking + instance profile the EC2 work-node
                # provider launches into (cleanup item 8b). Additive; GCP unaffected.
                work_node_subnet = outputs.get("work_node_subnet_id", {}).get("value", "")
                work_node_sg = outputs.get("work_node_security_group_id", {}).get("value", "")
                work_node_profile = outputs.get("work_node_instance_profile", {}).get("value", "")
                await _set_config(session, "aws_work_node_subnet_id", work_node_subnet or "null")
                await _set_config(session, "aws_work_node_security_group_id", work_node_sg or "null")
                await _set_config(session, "aws_work_node_instance_profile", work_node_profile or "null")

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
            compute_completed = event.resources_completed
            compute_planned = event.resources_total
            yield TerraformProgressEvent(
                event_type="phase_complete",
                message="Compute deployment complete",
                resources_completed=storage_completed + compute_completed,
                resources_total=storage_planned + compute_planned,
            )
        else:
            # Re-emit with accumulated totals so the progress bar
            # reflects the full stack, not just the current module.
            if event.event_type == "resource_complete":
                compute_completed += 1
            if event.resources_total:
                compute_planned = event.resources_total
            yield TerraformProgressEvent(
                event_type=event.event_type,
                message=event.message,
                resource_address=event.resource_address,
                resources_completed=storage_completed + compute_completed,
                resources_total=storage_planned + compute_planned,
                log_line=event.log_line,
                extra=event.extra,
            )

    # Restore default region/zone if we overrode them for compute
    if original_region is not None:
        await _set_config(session, "gcp_region", original_region)
    if original_zone is not None:
        await _set_config(session, "gcp_zone", original_zone)

    if compute_failed:
        # Log the expected cluster and its service accounts as orphaned
        project_id = await _read_config(session, "gcp_project_id")
        region = await _read_config(session, "gcp_region") or "us-central1"
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

    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Stack deployment complete",
        resources_completed=storage_completed + compute_completed,
        resources_total=storage_planned + compute_planned,
    )


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

    teardown_failed = False
    async for event in _run_destroy(session, user_id, "compute"):
        yield event
        if event.event_type == "apply_error":
            teardown_failed = True

    if teardown_failed:
        # Log the cluster and its service accounts as orphaned
        project_id = await _read_config(session, "gcp_project_id")
        region = await _read_config(session, "gcp_region") or "us-central1"
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

    # Clear GKE config
    await _set_config(session, "compute_deployed", "false")
    await _set_config(session, "gke_cluster_name", "null")
    await _set_config(session, "gke_cluster_endpoint", "null")
    await _set_config(session, "gke_cluster_ca_cert", "null")

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
    await session.flush()

    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Teardown complete",
    )


_BUCKET_CONFIG_KEYS = [
    "ingest_bucket_name",
    "raw_bucket_name",
    "working_bucket_name",
    "results_bucket_name",
    "references_bucket_name",
    "literature_bucket_name",
    "config_backups_bucket_name",
]


async def _empty_gcs_bucket(session: AsyncSession, bucket_name: str) -> int:
    """Delete all objects (including noncurrent versions) from a bucket.

    Returns the number of objects deleted. Routes through the storage adapter,
    which owns credential resolution; ``include_versions`` enumerates every
    generation and each is deleted by its generation id (versioned-wipe).
    """
    from app.adapters.registry import get_storage_adapter

    adapter = get_storage_adapter()
    uri_prefix = adapter.build_uri(bucket_name, "")

    deleted = 0
    for obj in await adapter.list_objects(uri_prefix, include_versions=True):
        generation = obj.provider_details.get("generation")
        await adapter.delete(obj.storage_uri, generation=generation)
        deleted += 1

    return deleted


async def destroy_storage(
    session: AsyncSession,
    user_id: int,
    org_id: int | None = None,
) -> AsyncGenerator[TerraformProgressEvent, None]:
    """Destroy the storage module (GCS buckets + Pub/Sub).

    Empties all GCS buckets before running terraform destroy (required
    because buckets have force_destroy=false). Clears all storage-related
    platform_config keys and resets stack_uid so a fresh deploy generates
    new resource names (avoids GCS soft-delete name conflicts).
    """
    compute_deployed = await _read_config(session, "compute_deployed")
    if compute_deployed == "true":
        raise StateError("Cannot destroy storage while compute stack is deployed. Teardown compute first.")

    storage_deployed = await _read_config(session, "storage_deployed")
    if storage_deployed != "true":
        raise StateError("Storage is not deployed.")

    # Step 1: Empty all GCS buckets so terraform destroy can remove them
    # (buckets have force_destroy=false and cannot be deleted while non-empty)
    for config_key in _BUCKET_CONFIG_KEYS:
        bucket_name = await _read_config(session, config_key)
        if not bucket_name or bucket_name == "null":
            continue
        yield TerraformProgressEvent(
            event_type="progress",
            message=f"Emptying bucket {bucket_name}...",
        )
        try:
            count = await _empty_gcs_bucket(session, bucket_name)
            logger.info("Emptied bucket %s (%d objects deleted)", bucket_name, count)
        except Exception as exc:
            logger.error("Failed to empty bucket %s: %s", bucket_name, exc)
            yield TerraformProgressEvent(
                event_type="stack_error",
                message=f"Failed to empty bucket {bucket_name}: {exc}",
            )
            return

    # Step 2: Mark all file records as storage_deleted. The metadata
    # (experiment links, checksums, upload history) is preserved but
    # the backing GCS objects no longer exist.
    result = await session.execute(text("UPDATE files SET storage_deleted = true WHERE storage_deleted = false"))
    marked_count = result.rowcount
    await session.flush()
    if marked_count:
        logger.info("Marked %d file(s) as storage_deleted", marked_count)

    # Step 2b (AWS only): destroy the image-build infrastructure (CodeBuild project
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
        ib_destroy_failed = False
        async for event in _run_destroy(session, user_id, "image_build"):
            yield event
            if event.event_type == "apply_error":
                ib_destroy_failed = True
        if ib_destroy_failed:
            yield TerraformProgressEvent(
                event_type="stack_error",
                message="Image build destroy failed",
            )
            return
        await _set_config(session, "aws_codebuild_project", "null")
        await _set_config(session, "aws_image_build_deployed", "false")
        await session.flush()

    # Step 3: Run terraform destroy on the now-empty buckets
    yield TerraformProgressEvent(
        event_type="progress",
        message="Destroying storage infrastructure...",
    )

    destroy_failed = False
    async for event in _run_destroy(session, user_id, "storage"):
        yield event
        if event.event_type == "apply_error":
            destroy_failed = True

    if destroy_failed:
        yield TerraformProgressEvent(
            event_type="stack_error",
            message="Storage destroy failed",
        )
        return

    # Clear all storage-related resource names from platform_config.
    # Next deploy generates a fresh suffix automatically.
    for key in [
        "storage_deployed",
        "ingest_bucket_name",
        "raw_bucket_name",
        "working_bucket_name",
        "results_bucket_name",
        "config_backups_bucket_name",
        "pubsub_topic_name",
        "pubsub_subscription_name",
    ]:
        await _set_config(session, key, "null")

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

    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Storage destroyed",
    )


_STORAGE_BUCKET_OUTPUT_KEYS = [
    "ingest_bucket_name",
    "raw_bucket_name",
    "working_bucket_name",
    "results_bucket_name",
    "references_bucket_name",
    "literature_bucket_name",
    "config_backups_bucket_name",
]


async def sync_storage_config(session: AsyncSession) -> dict[str, str]:
    """Re-read the storage Terraform outputs and write bucket names to platform_config.

    Used to recover deployments where storage was applied before the output-
    persistence fix was in place.  Returns a dict of {config_key: bucket_name}
    for all keys that were successfully populated.
    """
    outputs = await TerraformExecutor.read_module_outputs(session, "storage")
    populated: dict[str, str] = {}
    for key in _STORAGE_BUCKET_OUTPUT_KEYS:
        bucket_name = outputs.get(key, {}).get("value", "")
        if bucket_name:
            await _set_config(session, key, bucket_name)
            populated[key] = bucket_name
    await session.flush()
    return populated


_COMPUTE_OUTPUT_MAP = {
    "cluster_name": "gke_cluster_name",
    "cluster_endpoint": "gke_cluster_endpoint",
    "cluster_ca_cert": "gke_cluster_ca_cert",
    # Per-workload Workload Identity runner SA emails. Persisted here (not only
    # in the full-deploy hook) so the "Check for Infrastructure Updates" path
    # records a newly added runner SA -- e.g. cellxgene -- in platform_config;
    # the adapters read these to annotate their runner KSA.
    "notebook_runner_sa_email": "notebook_runner_sa_email",
    "cellxgene_runner_sa_email": "cellxgene_runner_sa_email",
}


async def sync_compute_config(session: AsyncSession) -> dict[str, str]:
    """Re-read the compute Terraform outputs and write cluster config to platform_config.

    Used to recover deployments where the terraform output capture failed
    silently, leaving gke_cluster_endpoint as 'null'. Returns a dict of
    {config_key: value} for all keys that were successfully populated.
    """
    outputs = await TerraformExecutor.read_module_outputs(session, "compute")
    populated: dict[str, str] = {}
    for tf_key, config_key in _COMPUTE_OUTPUT_MAP.items():
        value = outputs.get(tf_key, {}).get("value", "")
        if value:
            await _set_config(session, config_key, value)
            populated[config_key] = value
    await session.flush()
    return populated
