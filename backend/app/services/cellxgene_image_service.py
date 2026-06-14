"""Cellxgene image build service.

Manages the Cloud Build job for the cellxgene container image. Follows the
same pattern as notebook_image_service: embedded Dockerfile, GCS context
upload, Cloud Build submission, and polling.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.image_build import get_image_build_provider
from app.adapters.image_registry import get_image_registry_provider
from app.exceptions import ValidationError
from app.platform.platform_config_service import PlatformConfigService
from app.services.notebook_image_service import _get_credentials

logger = logging.getLogger("bioaf.cellxgene_image")

IMAGE_NAME = "bioaf-cellxgene"
IMAGE_TAG = "latest"

DOCKERFILE_CONTENT = """\
FROM python:3.11-slim

RUN pip install --no-cache-dir cellxgene gcsfs

EXPOSE 5005

ENTRYPOINT ["cellxgene"]
"""


def get_image_uri(project_id: str, region: str) -> str:
    """Construct the full image URI via the cloud-selected image registry."""
    return get_image_registry_provider().image_uri({"project_id": project_id, "region": region}, IMAGE_NAME, IMAGE_TAG)


async def _read_config(session: AsyncSession, key: str) -> str:
    value = await PlatformConfigService.get(session, key)
    return value if value is not None else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    await PlatformConfigService.set(session, key, value)


async def _upload_build_context(session: AsyncSession, project_id: str, working_bucket: str) -> str:
    """Create a tar.gz with the Dockerfile and upload to storage."""
    from app.adapters.registry import get_storage_adapter

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        dockerfile_bytes = DOCKERFILE_CONTENT.encode()
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_bytes)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(dockerfile_bytes))

    buf.seek(0)
    object_path = "builds/bioaf-cellxgene/source.tar.gz"
    adapter = get_storage_adapter()
    await adapter.upload_file(adapter.build_uri(working_bucket, object_path), buf, content_type="application/gzip")
    logger.info("Uploaded build context to the %s bucket at %s", working_bucket, object_path)

    return object_path


async def submit_image_build(session: AsyncSession, project_id: str, region: str) -> str:
    """Submit a Cloud Build job for the cellxgene image. Returns the build ID."""
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValidationError("Working bucket not configured. Deploy storage first.")

    from app.adapters.registry import get_storage_adapter

    object_path = await _upload_build_context(session, project_id, working_bucket)
    context_uri = get_storage_adapter().build_uri(working_bucket, object_path)

    image_uri = get_image_uri(project_id, region)
    credentials = await _get_credentials(session)

    sa_email = await _read_config(session, "gcp_service_account_email")
    if not sa_email or sa_email == "null":
        sa_email = getattr(credentials, "service_account_email", None)

    build_id = get_image_build_provider().submit_build(
        credentials,
        {"project_id": project_id, "region": region},
        context_object_uri=context_uri,
        image_uri=image_uri,
        build_sa=sa_email,
        timeout="3600s",
    )

    await _set_config(session, "cellxgene_image_build_id", build_id)
    await _set_config(session, "cellxgene_image_build_status", "WORKING")

    return build_id


async def check_build_status(session: AsyncSession, project_id: str, build_id: str) -> str:
    """Check the status of the image build via the image-build provider."""
    credentials = await _get_credentials(session)
    return get_image_build_provider().check_build_status(credentials, {"project_id": project_id}, build_id)


async def build_cellxgene_image(session: AsyncSession) -> str:
    """Full flow: ensure AR repo exists, submit build, return build ID.

    Called when the cellxgene component is enabled. The image URI is NOT
    written until the build succeeds (via poll_image_build).
    """
    from app.services.notebook_image_service import ensure_artifact_registry

    project_id = await _read_config(session, "gcp_project_id")
    region = await _read_config(session, "gcp_region")

    if not project_id or project_id == "null":
        raise ValidationError("GCP project not configured")
    if not region or region == "null":
        raise ValidationError("GCP region not configured")

    await _set_config(session, "cellxgene_image", "null")
    await _set_config(session, "cellxgene_image_build_status", "null")
    await _set_config(session, "cellxgene_image_build_id", "null")

    # Reuse the shared AR repo
    await ensure_artifact_registry(session, project_id, region)

    build_id = await submit_image_build(session, project_id, region)
    return build_id


async def poll_image_build(session: AsyncSession) -> str | None:
    """Check if there is an active cellxgene image build and update its status.

    Called by the background task loop. Returns the current status
    or None if no active build.
    """
    build_id = await _read_config(session, "cellxgene_image_build_id")
    if not build_id or build_id == "null":
        return None

    current_status = await _read_config(session, "cellxgene_image_build_status")
    if current_status in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
        return current_status

    project_id = await _read_config(session, "gcp_project_id")
    if not project_id or project_id == "null":
        return None

    status = await check_build_status(session, project_id, build_id)
    await _set_config(session, "cellxgene_image_build_status", status)

    if status == "SUCCESS":
        logger.info("Cellxgene image build %s completed successfully", build_id)
        region = await _read_config(session, "gcp_region")
        image_uri = get_image_uri(project_id, region)
        await _set_config(session, "cellxgene_image", image_uri)
        # Drain the wizard queue: a queued cellxgene now has its image.
        # Local import to avoid the cycle through component_queue.
        from app.services.component_queue import process_queued_components

        await process_queued_components(session)
        await session.execute(
            text("""
            UPDATE component_states SET status = 'enabled'
            WHERE component_key = 'cellxgene'
            AND enabled = true AND status = 'provisioning'
            """)
        )
    elif status in ("FAILURE", "CANCELLED", "TIMEOUT"):
        logger.error("Cellxgene image build %s failed with status %s", build_id, status)
        await _set_config(session, "cellxgene_image", "null")
        await session.execute(
            text("""
            UPDATE component_states SET status = 'build_failed'
            WHERE component_key = 'cellxgene'
            AND enabled = true AND status = 'provisioning'
            """)
        )

    await session.flush()
    return status
