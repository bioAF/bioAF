"""ImageRegistry provider seam (Stage 4e).

The backend-aware seam for the container-image registry: construct an image URI
and ensure the destination repository exists. Pairs with the ImageBuild seam (the
build pushes to the URI this seam constructs).

GCP (Artifact Registry) builds ``{region}-docker.pkg.dev/{project}/{repo}/{name}:
{tag}`` and creates a shared DOCKER repo. AWS (ECR, Stage 6e) builds
``{account}.dkr.ecr.{region}.amazonaws.com/{repo}:{tag}`` and creates one
repository per image, behind this same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ImageRegistryProvider(ABC):
    """Cloud-specific image-URI construction + repository provisioning."""

    @abstractmethod
    def image_uri(self, config: dict, name: str, tag: str) -> str:
        """Full registry URI for image ``name:tag`` (``config`` carries project/region)."""

    @abstractmethod
    def ensure_repository(self, credentials: Any, config: dict, name: str) -> str:
        """Create the destination repository if absent; return its resource name.

        Idempotent. GCP uses a single shared repo (``name`` is informational);
        ECR creates one repo per image ``name``.
        """
