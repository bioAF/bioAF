"""ImageBuild provider factory (Stage 4e).

The backend-aware seam for the container-image build lifecycle. Selected from
``cloud_provider`` like the other substrate seams: ``get_image_build_provider``
reads the resolved-backend cache (``backend_for('image_build')``), defaulting to
Cloud Build on an unconfigured / GCP install. AWS (CodeBuild) adds a ``codebuild``
branch here behind the same interface in Stage 6e.
"""

from __future__ import annotations

from app.adapters.image_build.base import (
    BUILD_STATUSES,
    ImageBuildProvider,
)
from app.exceptions import ValidationError

__all__ = [
    "BUILD_STATUSES",
    "ImageBuildProvider",
    "VALID_IMAGE_BUILD_BACKENDS",
    "DEFAULT_IMAGE_BUILD_BACKEND",
    "create_image_build_provider",
    "get_image_build_provider",
]

VALID_IMAGE_BUILD_BACKENDS = ("cloud_build",)
DEFAULT_IMAGE_BUILD_BACKEND = "cloud_build"


def create_image_build_provider(backend: str = DEFAULT_IMAGE_BUILD_BACKEND) -> ImageBuildProvider:
    """Instantiate the image-build provider for ``backend`` (default Cloud Build)."""
    if backend not in VALID_IMAGE_BUILD_BACKENDS:
        raise ValidationError(f"Unknown image_build backend '{backend}'. Valid options: {VALID_IMAGE_BUILD_BACKENDS}")
    from app.adapters.image_build.gcp import GcpCloudBuildProvider

    return GcpCloudBuildProvider()


def get_image_build_provider() -> ImageBuildProvider:
    """Resolve the image-build provider for this install's cloud_provider.

    Reads the resolved-backend cache (loaded at adapter init); falls back to the
    Cloud Build default when the cache is unloaded (pre-DB bootstrap, local dev,
    tests), so behavior is unchanged on a GCP install.
    """
    from app.platform.cloud_provider import backend_for

    return create_image_build_provider(backend_for("image_build"))
