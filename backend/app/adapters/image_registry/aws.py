"""ECR realization of the ImageRegistry seam (Stage 6e).

The AWS sibling of ``GcpArtifactRegistryProvider``. ECR differs from Artifact
Registry in two ways the seam already anticipates: the image URI is
``{account}.dkr.ecr.{region}.amazonaws.com/{name}:{tag}`` (no shared repo path
segment), and there is **one repository per image** rather than a single shared
DOCKER repo, so ``ensure_repository`` creates a repo named after the image. boto3
lives behind this adapter boundary, exactly like ``S3StorageProvider`` and
``EksClusterAuthProvider``; the pod / VM authenticate ambiently through the
instance profile (no GCP-style credentials object is threaded).
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.image_registry.base import ImageRegistryProvider

logger = logging.getLogger("bioaf.image_registry")


class EcrImageRegistryProvider(ImageRegistryProvider):
    """Amazon ECR: ``{account}.dkr.ecr...`` URIs + per-image repository ensure."""

    def _client(self, region: str):
        """Construct the boto3 ECR client (lazy import; ambient credentials).

        boto3 is imported only here so the SDK stays inside the adapter layer
        (the BAL guard forbids it elsewhere). The pod / VM authenticates through
        its instance-profile / IRSA role; no explicit credentials are passed.
        """
        import boto3

        return boto3.client("ecr", region_name=region or None)

    def image_uri(self, config: dict, name: str, tag: str) -> str:
        return f"{config['account_id']}.dkr.ecr.{config['region']}.amazonaws.com/{name}:{tag}"

    def ensure_repository(self, credentials: Any, config: dict, name: str) -> str:
        """Create the ECR repository ``name`` if absent (idempotent).

        Unlike Artifact Registry's single shared repo, ECR addresses each image
        by its own repository, so ``name`` is the repository. A concurrent /
        prior create surfaces as ``RepositoryAlreadyExistsException``, which is
        swallowed; any other error propagates.
        """
        from botocore.exceptions import ClientError

        ecr = self._client(config["region"])
        try:
            ecr.create_repository(repositoryName=name)
            logger.info("Created ECR repository %s", name)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "RepositoryAlreadyExistsException":
                logger.info("ECR repository %s already exists", name)
            else:
                raise
        return name
