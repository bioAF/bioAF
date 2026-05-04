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

from app.services.activity_feed_service import ActivityFeedService
from app.services.audit_service import log_action
from app.services.orphaned_resource_service import OrphanedResourceService
from app.services.terraform_executor import TerraformExecutor, TerraformProgressEvent

logger = logging.getLogger("bioaf.stack_deployment")


# -----------------------------------------------------------------------
# Pydantic models for cluster status
# -----------------------------------------------------------------------

# GKE cluster status enum mapping
_GKE_STATUS_MAP = {
    0: "STATUS_UNSPECIFIED",
    1: "PROVISIONING",
    2: "RUNNING",
    3: "RECONCILING",
    4: "STOPPING",
    5: "ERROR",
    6: "DEGRADED",
}


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

    Returns google.oauth2 Credentials or None to fall back to ADC.
    Same pattern as GcsStorageService.get_credentials().
    """
    import json as _json

    result = await session.execute(
        text("SELECT key, value FROM platform_config WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')")
    )
    config = {r[0]: r[1] for r in result.fetchall()}

    if config.get("gcp_credential_source") != "service_account_key":
        return None

    key_json = config.get("gcp_service_account_key")
    if not key_json or key_json == "null":
        return None

    try:
        from google.oauth2 import service_account

        key_data = _json.loads(key_json)
        return service_account.Credentials.from_service_account_info(
            key_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    except Exception as e:
        logger.warning("Failed to load GKE credentials from platform_config: %s", e)
        return None


def _get_gke_client(credentials=None):
    """Get a GKE ClusterManager client. Tests mock this function."""
    from google.cloud import container_v1

    if credentials:
        return container_v1.ClusterManagerClient(credentials=credentials)
    return container_v1.ClusterManagerClient()


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

    # Query GKE API for cluster details
    cluster_name = await _read_config(session, "gke_cluster_name")
    project_id = await _read_config(session, "gcp_project_id")
    region = await _read_config(session, "gcp_region") or "us-central1"

    try:
        credentials = await _get_gke_credentials(session)
        client = _get_gke_client(credentials)
        cluster = client.get_cluster(name=f"projects/{project_id}/locations/{region}/clusters/{cluster_name}")

        pipeline_pool = None
        interactive_pool = None

        for pool in cluster.node_pools:
            pool_status = _GKE_STATUS_MAP.get(pool.status, "UNKNOWN")
            pool_info = NodePoolStatus(
                name=pool.name,
                machine_type=pool.config.machine_type,
                min_nodes=pool.autoscaling.min_node_count,
                max_nodes=pool.autoscaling.max_node_count,
                current_nodes=pool.initial_node_count,
                spot=pool.config.spot,
                status=pool_status,
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
            cluster_name=cluster.name,
            status=_GKE_STATUS_MAP.get(cluster.status, "UNKNOWN"),
            node_count=cluster.current_node_count,
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
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).fetchone()
    return row[0] if row else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a platform_config key."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ).bindparams(k=key, v=value)
    )


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
    # Validate pre-conditions
    gcp_configured = await _read_config(session, "gcp_credentials_configured")
    if gcp_configured != "true":
        raise ValueError("GCP credentials are not configured")

    tf_initialized = await _read_config(session, "terraform_initialized")
    if tf_initialized != "true":
        raise ValueError("Terraform has not been initialized")

    compute_deployed = await _read_config(session, "compute_deployed")
    if compute_deployed == "true":
        raise ValueError("Compute stack is already deployed. Teardown first.")

    if stack_type != "kubernetes":
        raise ValueError(f"Unsupported stack type: {stack_type}")

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
                    "config_backups_bucket_name",
                    "pubsub_topic_name",
                    "pubsub_subscription_name",
                ]:
                    output_val = outputs.get(config_key, {}).get("value", "")
                    if output_val:
                        await _set_config(session, config_key, output_val)
                await _set_config(session, "storage_deployed", "true")
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

    # Step 2: Deploy compute
    # If the user chose a different region/zone for compute, temporarily
    # override the config so _write_tfvars picks up the override values.
    # Restore defaults after deploy completes (success or failure).
    original_region = None
    original_zone = None
    if compute_region:
        original_region = await _read_config(session, "gcp_region")
        await _set_config(session, "gcp_region", compute_region)
        if not compute_zone:
            from app.gcp_zones import default_zone

            compute_zone = default_zone(compute_region)
    if compute_zone:
        original_zone = await _read_config(session, "gcp_zone")
        await _set_config(session, "gcp_zone", compute_zone)

    await _set_config(session, "deploy_suffix", secrets.token_hex(3))
    await session.flush()
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

            await _set_config(session, "compute_stack", "kubernetes")
            await _set_config(session, "compute_deployed", "true")
            await _set_config(session, "gke_cluster_name", cluster_name or "null")
            await _set_config(session, "gke_cluster_endpoint", cluster_endpoint or "null")
            await _set_config(session, "gke_cluster_ca_cert", cluster_ca_cert or "null")
            await _set_config(session, "notebook_runner_sa_email", notebook_runner_sa or "null")

            # Update kubernetes_cluster component state
            await session.execute(
                text("""
                UPDATE component_states
                SET enabled = true, status = 'running'
                WHERE component_key = 'kubernetes_cluster'
                """)
            )
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

    yield TerraformProgressEvent(
        event_type="stack_complete",
        message="Stack deployment complete",
        resources_completed=storage_completed + compute_completed,
        resources_total=storage_planned + compute_planned,
    )


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
        raise ValueError("Compute stack is not deployed")

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
    "config_backups_bucket_name",
]


async def _empty_gcs_bucket(session: AsyncSession, bucket_name: str) -> int:
    """Delete all objects (including noncurrent versions) from a GCS bucket.

    Returns the number of objects deleted. Uses the same credential
    resolution as GcsStorageService so it works with SA keys stored
    in platform_config.
    """
    from google.cloud import storage as gcs_storage

    from app.services.gcs_storage import GcsStorageService

    credentials = await GcsStorageService.get_credentials(session)
    client = gcs_storage.Client(credentials=credentials)

    deleted = 0
    # Delete current objects
    for blob in client.list_blobs(bucket_name):
        blob.delete()
        deleted += 1
    # Delete noncurrent versions (versioned buckets retain old copies)
    for blob in client.list_blobs(bucket_name, versions=True):
        blob.delete()
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
        raise ValueError("Cannot destroy storage while compute stack is deployed. Teardown compute first.")

    storage_deployed = await _read_config(session, "storage_deployed")
    if storage_deployed != "true":
        raise ValueError("Storage is not deployed.")

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
