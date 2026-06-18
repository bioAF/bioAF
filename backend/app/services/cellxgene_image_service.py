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
from app.services.image_build_platform import ImagePlatform, resolve_image_credentials, resolve_image_platform

logger = logging.getLogger("bioaf.cellxgene_image")

IMAGE_NAME = "bioaf-cellxgene"
IMAGE_TAG = "latest"

DOCKERFILE_CONTENT = """\
FROM python:3.11-slim

RUN pip install --no-cache-dir cellxgene gcsfs

EXPOSE 5005

ENTRYPOINT ["cellxgene"]
"""


def get_image_uri(config: dict) -> str:
    """Construct the full image URI via the cloud-selected image registry.

    ``config`` is the cloud-resolved provider config (``ImagePlatform.config``).
    """
    return get_image_registry_provider().image_uri(config, IMAGE_NAME, IMAGE_TAG)


async def ensure_image_repository(session: AsyncSession, platform: ImagePlatform) -> str:
    """Create the cellxgene image repository if absent (idempotent).

    Uses the cellxgene ``IMAGE_NAME`` so AWS ensures the ``bioaf-cellxgene`` ECR
    repo (ECR is per-image); on GCP this is the shared Artifact Registry repo.
    """
    credentials = await resolve_image_credentials(session, platform)
    return get_image_registry_provider().ensure_repository(credentials, platform.config, IMAGE_NAME)


async def _read_config(session: AsyncSession, key: str) -> str:
    value = await PlatformConfigService.get(session, key)
    return value if value is not None else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    await PlatformConfigService.set(session, key, value)


async def _upload_build_context(session: AsyncSession, working_bucket: str) -> str:
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


async def submit_image_build(session: AsyncSession, platform: ImagePlatform) -> str:
    """Submit a build job for the cellxgene image. Returns the build ID."""
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValidationError("Working bucket not configured. Deploy storage first.")

    from app.adapters.registry import get_storage_adapter

    object_path = await _upload_build_context(session, working_bucket)
    context_uri = get_storage_adapter().build_uri(working_bucket, object_path)

    image_uri = get_image_uri(platform.config)
    credentials = await resolve_image_credentials(session, platform)

    build_sa = platform.build_sa
    if platform.cloud_provider != "aws" and not build_sa:
        build_sa = getattr(credentials, "service_account_email", None)

    build_id = get_image_build_provider().submit_build(
        credentials,
        platform.config,
        context_object_uri=context_uri,
        image_uri=image_uri,
        build_sa=build_sa,
        timeout="3600s",
    )

    await _set_config(session, "cellxgene_image_build_id", build_id)
    await _set_config(session, "cellxgene_image_build_status", "WORKING")

    return build_id


async def check_build_status(session: AsyncSession, build_id: str) -> str:
    """Check the status of the image build via the image-build provider."""
    platform = await resolve_image_platform(session)
    credentials = await resolve_image_credentials(session, platform)
    return get_image_build_provider().check_build_status(credentials, platform.config, build_id)


async def build_cellxgene_image(session: AsyncSession) -> str:
    """Full flow: ensure the image repo exists, submit build, return build ID.

    Called when the cellxgene component is enabled. The image URI is NOT
    written until the build succeeds (via poll_image_build).
    """
    platform = await resolve_image_platform(session)
    platform.require_target()
    platform.require_build_service()

    await _set_config(session, "cellxgene_image", "null")
    await _set_config(session, "cellxgene_image_build_status", "null")
    await _set_config(session, "cellxgene_image_build_id", "null")

    # Ensure the cellxgene image repository (shared AR repo on GCP; the
    # bioaf-cellxgene ECR repo on AWS).
    await ensure_image_repository(session, platform)

    build_id = await submit_image_build(session, platform)
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

    platform = await resolve_image_platform(session)
    if not platform.has_target:
        return None

    status = await check_build_status(session, build_id)
    await _set_config(session, "cellxgene_image_build_status", status)

    if status == "SUCCESS":
        logger.info("Cellxgene image build %s completed successfully", build_id)
        image_uri = get_image_uri(platform.config)
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
