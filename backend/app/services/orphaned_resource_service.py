"""Service for tracking and cleaning up orphaned GCP resources."""

import logging
from datetime import datetime, timezone

from google.cloud import storage
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orphaned_resource import OrphanedResource

logger = logging.getLogger(__name__)

# GKE cluster status enum mapping (mirrors stack_deployment._GKE_STATUS_MAP)
_GKE_STATUS_MAP = {
    0: "STATUS_UNSPECIFIED",
    1: "PROVISIONING",
    2: "RUNNING",
    3: "RECONCILING",
    4: "STOPPING",
    5: "ERROR",
    6: "DEGRADED",
}

# Statuses that indicate the cluster is alive and usable
_RECOVERABLE_STATUSES = {"RUNNING", "RECONCILING"}

# Statuses that indicate the cluster is not yet ready but still starting
_PROVISIONING_STATUSES = {"PROVISIONING"}

# Statuses that indicate the cluster is dead or should be cleaned up
_DEAD_STATUSES = {"ERROR", "DEGRADED", "STOPPING", "STATUS_UNSPECIFIED"}


def _get_gke_client(credentials=None):
    """Get a GKE ClusterManager client. Tests mock this function."""
    from google.cloud import container_v1

    if credentials:
        return container_v1.ClusterManagerClient(credentials=credentials)
    return container_v1.ClusterManagerClient()


async def _get_gke_credentials(session: AsyncSession):
    """Read SA credentials from platform_config for GKE API calls."""
    from app.services.stack_deployment import _get_gke_credentials as _get_creds

    return await _get_creds(session)


class OrphanedResourceService:
    """Track and clean up GCP resources left behind by failed Terraform runs."""

    @staticmethod
    async def log_resource(
        session: AsyncSession,
        resource_type: str,
        resource_name: str,
        gcp_project_id: str,
        stack_uid: str,
        gcp_zone: str | None = None,
        terraform_run_id: int | None = None,
    ) -> OrphanedResource:
        """Record a detected orphaned resource."""
        resource = OrphanedResource(
            resource_type=resource_type,
            resource_name=resource_name,
            gcp_project_id=gcp_project_id,
            gcp_zone=gcp_zone,
            stack_uid=stack_uid,
            terraform_run_id=terraform_run_id,
            status="detected",
        )
        session.add(resource)
        await session.flush()
        return resource

    @staticmethod
    async def list_resources(
        session: AsyncSession,
        status: str | None = None,
    ) -> list[OrphanedResource]:
        """List orphaned resources, optionally filtered by status."""
        stmt = select(OrphanedResource).order_by(OrphanedResource.detected_at.desc())
        if status:
            stmt = stmt.where(OrphanedResource.status == status)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def has_orphaned_for_uid(
        session: AsyncSession,
        stack_uid: str,
    ) -> bool:
        """Check if any unresolved orphaned resources exist for a stack_uid."""
        unresolved = {"detected", "failed"}
        stmt = (
            select(OrphanedResource.id)
            .where(
                OrphanedResource.stack_uid == stack_uid,
                OrphanedResource.status.in_(unresolved),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def cleanup_resource(
        session: AsyncSession,
        resource_id: int,
        user_id: int,
    ) -> OrphanedResource:
        """Dispatch cleanup based on resource_type, update status."""
        result = await session.execute(select(OrphanedResource).where(OrphanedResource.id == resource_id))
        resource = result.scalar_one_or_none()
        if not resource:
            raise ValueError(f"Orphaned resource {resource_id} not found")

        resource.status = "cleaning"
        await session.flush()

        try:
            if resource.resource_type == "gke_cluster":
                await OrphanedResourceService._cleanup_gke_cluster(session, resource)
            elif resource.resource_type == "gcs_bucket":
                await OrphanedResourceService._cleanup_gcs_bucket(session, resource)
            else:
                raise ValueError(f"Unknown resource type: {resource.resource_type}")

            resource.status = "cleaned"
            resource.resolved_at = datetime.now(timezone.utc)
            resource.resolved_by_user_id = user_id
            logger.info("Cleaned up orphaned %s: %s", resource.resource_type, resource.resource_name)
        except Exception as exc:
            resource.status = "failed"
            resource.error_message = str(exc)
            logger.warning(
                "Failed to clean up orphaned %s %s: %s",
                resource.resource_type,
                resource.resource_name,
                exc,
            )

        await session.flush()
        return resource

    @staticmethod
    async def _cleanup_gke_cluster(
        session: AsyncSession,
        resource: OrphanedResource,
    ) -> None:
        """Delete a GKE cluster using the SA credentials."""
        from app.services.stack_deployment import _get_gke_client, _get_gke_credentials

        credentials = await _get_gke_credentials(session)
        client = _get_gke_client(credentials)
        cluster_path = (
            f"projects/{resource.gcp_project_id}/locations/{resource.gcp_zone}/clusters/{resource.resource_name}"
        )
        client.delete_cluster(name=cluster_path)

    @staticmethod
    async def _cleanup_gcs_bucket(
        session: AsyncSession,
        resource: OrphanedResource,
    ) -> None:
        """Delete a GCS bucket using the SA credentials."""
        from app.services.stack_deployment import _get_gke_credentials

        credentials = await _get_gke_credentials(session)
        if credentials:
            client = storage.Client(credentials=credentials, project=resource.gcp_project_id)
        else:
            client = storage.Client(project=resource.gcp_project_id)
        bucket = client.bucket(resource.resource_name)
        bucket.delete(force=True)

    @staticmethod
    async def dismiss_resource(
        session: AsyncSession,
        resource_id: int,
        user_id: int,
    ) -> OrphanedResource:
        """Mark an orphaned resource as manually resolved."""
        result = await session.execute(select(OrphanedResource).where(OrphanedResource.id == resource_id))
        resource = result.scalar_one_or_none()
        if not resource:
            raise ValueError(f"Orphaned resource {resource_id} not found")

        resource.status = "dismissed"
        resource.resolved_at = datetime.now(timezone.utc)
        resource.resolved_by_user_id = user_id
        await session.flush()
        return resource

    @staticmethod
    async def _query_gke_status(
        session: AsyncSession,
        resource: OrphanedResource,
    ) -> tuple[str, object | None]:
        """Query GKE API for the live status of an orphaned cluster.

        Returns (status_string, cluster_object_or_None).
        If the cluster cannot be found, returns ("NOT_FOUND", None).
        """
        credentials = await _get_gke_credentials(session)
        client = _get_gke_client(credentials)
        cluster_path = (
            f"projects/{resource.gcp_project_id}/locations/{resource.gcp_zone}/clusters/{resource.resource_name}"
        )
        try:
            cluster = client.get_cluster(name=cluster_path)
            status_str = _GKE_STATUS_MAP.get(cluster.status, "UNKNOWN")
            return status_str, cluster
        except Exception as exc:
            logger.info(
                "GKE cluster %s not reachable: %s",
                resource.resource_name,
                exc,
            )
            return "NOT_FOUND", None

    @staticmethod
    async def recovery_check(
        session: AsyncSession,
    ) -> dict[str, list[dict]]:
        """Check all unresolved orphaned GKE clusters and classify them.

        Returns a dict with three lists:
        - recoverable: clusters in RUNNING/RECONCILING state (can be adopted)
        - provisioning: clusters still being created
        - dead: clusters in ERROR state or not found in GCP
        """
        unresolved = {"detected", "failed"}
        stmt = (
            select(OrphanedResource)
            .where(
                OrphanedResource.resource_type == "gke_cluster",
                OrphanedResource.status.in_(unresolved),
            )
            .order_by(OrphanedResource.detected_at.desc())
        )
        result = await session.execute(stmt)
        resources = list(result.scalars().all())

        recoverable: list[dict] = []
        provisioning: list[dict] = []
        dead: list[dict] = []

        for resource in resources:
            gke_status, _cluster = await OrphanedResourceService._query_gke_status(session, resource)

            entry = {
                "id": resource.id,
                "resource_name": resource.resource_name,
                "gcp_project_id": resource.gcp_project_id,
                "gcp_zone": resource.gcp_zone,
                "stack_uid": resource.stack_uid,
                "gke_status": gke_status,
                "detected_at": resource.detected_at.isoformat() if resource.detected_at else None,
            }

            if gke_status in _RECOVERABLE_STATUSES:
                recoverable.append(entry)
            elif gke_status in _PROVISIONING_STATUSES:
                provisioning.append(entry)
            else:
                dead.append(entry)

        return {
            "recoverable": recoverable,
            "provisioning": provisioning,
            "dead": dead,
        }

    @staticmethod
    async def adopt_resource(
        session: AsyncSession,
        resource_id: int,
        user_id: int,
    ) -> OrphanedResource:
        """Adopt an orphaned GKE cluster that is actually running.

        Queries GKE API to confirm the cluster is in a running state,
        then populates platform_config with its details and marks the
        orphaned resource as adopted.
        """
        result = await session.execute(select(OrphanedResource).where(OrphanedResource.id == resource_id))
        resource = result.scalar_one_or_none()
        if not resource:
            raise ValueError(f"Orphaned resource {resource_id} not found")

        if resource.resource_type != "gke_cluster":
            raise ValueError(f"Only GKE clusters can be adopted, got {resource.resource_type}")

        gke_status, cluster = await OrphanedResourceService._query_gke_status(session, resource)

        if gke_status not in _RECOVERABLE_STATUSES:
            raise ValueError(
                f"Cluster {resource.resource_name} is not in a running state (current status: {gke_status})"
            )

        # Populate platform_config with cluster details
        from app.services.stack_deployment import _set_config

        await _set_config(session, "compute_stack", "kubernetes")
        await _set_config(session, "compute_deployed", "true")
        await _set_config(session, "gke_cluster_name", resource.resource_name)

        if cluster:
            endpoint = getattr(cluster, "endpoint", "") or ""
            ca_cert = ""
            if hasattr(cluster, "master_auth") and cluster.master_auth:
                ca_cert = getattr(cluster.master_auth, "cluster_ca_certificate", "") or ""
            await _set_config(session, "gke_cluster_endpoint", endpoint or "null")
            await _set_config(session, "gke_cluster_ca_cert", ca_cert or "null")

        # Update kubernetes_cluster component state
        await session.execute(
            text("""
            UPDATE component_states
            SET enabled = true, status = 'running'
            WHERE component_key = 'kubernetes_cluster'
            """)
        )

        # Log the adoption in audit
        from app.services.audit_service import log_action

        await log_action(
            session,
            user_id=user_id,
            entity_type="infrastructure",
            entity_id=0,
            action="adopt_orphaned_cluster",
            details={
                "cluster_name": resource.resource_name,
                "gke_status": gke_status,
            },
        )

        resource.status = "adopted"
        resource.resolved_at = datetime.now(timezone.utc)
        resource.resolved_by_user_id = user_id
        await session.flush()

        logger.info(
            "Adopted orphaned GKE cluster %s (status: %s)",
            resource.resource_name,
            gke_status,
        )
        return resource

    @staticmethod
    async def cleanup_dead_orphans(
        session: AsyncSession,
        user_id: int,
    ) -> dict[str, int]:
        """Clean up all orphaned GKE clusters that are dead (ERROR or NOT_FOUND).

        Skips clusters that are RUNNING (should be adopted) or PROVISIONING
        (still starting). For NOT_FOUND clusters, just marks them dismissed.
        For ERROR clusters, attempts to delete then marks cleaned.

        Returns counts: {"cleaned": N, "skipped": N, "failed": N}.
        """
        unresolved = {"detected", "failed"}
        stmt = select(OrphanedResource).where(
            OrphanedResource.resource_type == "gke_cluster",
            OrphanedResource.status.in_(unresolved),
        )
        result = await session.execute(stmt)
        resources = list(result.scalars().all())

        cleaned = 0
        skipped = 0
        failed = 0

        for resource in resources:
            gke_status, _cluster = await OrphanedResourceService._query_gke_status(session, resource)

            if gke_status in _RECOVERABLE_STATUSES or gke_status in _PROVISIONING_STATUSES:
                skipped += 1
                continue

            if gke_status == "NOT_FOUND":
                # Cluster is already gone, just dismiss the record
                resource.status = "cleaned"
                resource.resolved_at = datetime.now(timezone.utc)
                resource.resolved_by_user_id = user_id
                cleaned += 1
                continue

            # Cluster exists but is in a bad state -- try to delete it
            try:
                credentials = await _get_gke_credentials(session)
                client = _get_gke_client(credentials)
                cluster_path = (
                    f"projects/{resource.gcp_project_id}"
                    f"/locations/{resource.gcp_zone}"
                    f"/clusters/{resource.resource_name}"
                )
                client.delete_cluster(name=cluster_path)
                resource.status = "cleaned"
                resource.resolved_at = datetime.now(timezone.utc)
                resource.resolved_by_user_id = user_id
                cleaned += 1
            except Exception as exc:
                resource.status = "failed"
                resource.error_message = str(exc)
                failed += 1
                logger.warning(
                    "Failed to delete orphaned cluster %s: %s",
                    resource.resource_name,
                    exc,
                )

        await session.flush()
        return {"cleaned": cleaned, "skipped": skipped, "failed": failed}
