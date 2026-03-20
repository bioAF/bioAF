"""Service for tracking and cleaning up orphaned GCP resources."""

import logging
from datetime import datetime, timezone

from google.cloud import storage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orphaned_resource import OrphanedResource

logger = logging.getLogger(__name__)


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
