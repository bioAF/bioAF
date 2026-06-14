"""ImageRegistry provider factory (Stage 4e).

The backend-aware seam for the container-image registry. Selected from
``cloud_provider`` like the other substrate seams: ``get_image_registry_provider``
reads the resolved-backend cache (``backend_for('image_registry')``), defaulting
to Artifact Registry on an unconfigured / GCP install. AWS (ECR) adds an ``ecr``
branch here behind the same interface in Stage 6e.
"""

from __future__ import annotations

from app.adapters.image_registry.base import ImageRegistryProvider
from app.exceptions import ValidationError

VALID_IMAGE_REGISTRY_BACKENDS = ("artifact_registry",)
DEFAULT_IMAGE_REGISTRY_BACKEND = "artifact_registry"


def create_image_registry_provider(backend: str = DEFAULT_IMAGE_REGISTRY_BACKEND) -> ImageRegistryProvider:
    """Instantiate the image-registry provider for ``backend`` (default Artifact Registry)."""
    if backend not in VALID_IMAGE_REGISTRY_BACKENDS:
        raise ValidationError(
            f"Unknown image_registry backend '{backend}'. Valid options: {VALID_IMAGE_REGISTRY_BACKENDS}"
        )
    from app.adapters.image_registry.gcp import GcpArtifactRegistryProvider

    return GcpArtifactRegistryProvider()


def get_image_registry_provider() -> ImageRegistryProvider:
    """Resolve the image-registry provider for this install's cloud_provider.

    Reads the resolved-backend cache (loaded at adapter init); falls back to the
    Artifact Registry default when the cache is unloaded (pre-DB bootstrap, local
    dev, tests), so behavior is unchanged on a GCP install.
    """
    from app.platform.cloud_provider import backend_for

    return create_image_registry_provider(backend_for("image_registry"))
