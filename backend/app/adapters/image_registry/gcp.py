"""Artifact Registry realization of the ImageRegistry seam (Stage 4e).

Owns the Artifact Registry image-URI format and the repo-ensure REST relocated
from notebook_image_service. ``AR_REPO_ID`` is the single shared Docker repo all
bioAF images live in; it is re-exported by the image services for the work-node /
conda image builds that still construct AR URIs directly.
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.image_build.gcp import authorized_request
from app.adapters.image_registry.base import ImageRegistryProvider

logger = logging.getLogger("bioaf.image_registry")

# The shared Artifact Registry Docker repository all bioAF images live in.
AR_REPO_ID = "bioaf-images"

_AR_API = "https://artifactregistry.googleapis.com/v1"


class GcpArtifactRegistryProvider(ImageRegistryProvider):
    """Artifact Registry: ``{region}-docker.pkg.dev/...`` URIs + DOCKER repo ensure."""

    def image_uri(self, config: dict, name: str, tag: str) -> str:
        return f"{config['region']}-docker.pkg.dev/{config['project_id']}/{AR_REPO_ID}/{name}:{tag}"

    def ensure_repository(self, credentials: Any, config: dict, name: str) -> str:
        project_id = config["project_id"]
        region = config["region"]
        parent = f"projects/{project_id}/locations/{region}"
        repo_name = f"{parent}/repositories/{AR_REPO_ID}"

        # Check if repo exists
        url = f"{_AR_API}/{repo_name}"
        try:
            authorized_request(credentials, "GET", url)
            logger.info("Artifact Registry repo %s already exists", repo_name)
            return repo_name
        except Exception:
            pass  # 404 expected, create it

        # Create repo
        create_url = f"{_AR_API}/{parent}/repositories?repositoryId={AR_REPO_ID}"
        body = {
            "format": "DOCKER",
            "description": "bioAF container images for notebook environments",
        }
        try:
            authorized_request(credentials, "POST", create_url, body)
            logger.info("Created Artifact Registry repo %s", repo_name)
        except Exception as e:
            # May be ALREADY_EXISTS race or permission error
            logger.warning("Artifact Registry create returned error (may already exist): %s", e)

        return repo_name
