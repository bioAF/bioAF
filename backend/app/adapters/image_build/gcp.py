"""Cloud Build realization of the ImageBuild seam (Stage 4e).

Holds the Cloud Build REST shaping relocated from notebook_image_service /
cellxgene_image_service so the service layer names no cloud. The module-level
``authorized_request`` (a GCP-authenticated REST call) is the shared primitive
the Artifact Registry provider and the still-GCP-coupled work-node image builds
re-use.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.adapters.image_build.base import ImageBuildProvider

logger = logging.getLogger("bioaf.image_build")

_CLOUD_BUILD_API = "https://cloudbuild.googleapis.com/v1"


def authorized_request(credentials: Any, method: str, url: str, body: dict | None = None) -> dict:
    """Make an authenticated HTTP request to a GCP REST API.

    Mints a fresh bearer token via the credentials seam and issues a urllib
    request. Relocated from the image services (where it was duplicated) so it is
    owned by the adapter layer; the services re-export it for the work-node /
    conda image builds that still call Cloud Build directly.
    """
    import urllib.request

    from app.adapters.credentials import get_credentials_provider

    token = get_credentials_provider().bearer_token(credentials)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        logger.error("GCP API %s %s -> %d: %s", method, url, e.code, error_body)
        raise


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    """Split a ``gs://bucket/object`` URI into ``(bucket, object)``."""
    path = uri[len("gs://") :] if uri.startswith("gs://") else uri
    bucket, _, obj = path.partition("/")
    return bucket, obj


class GcpCloudBuildProvider(ImageBuildProvider):
    """Cloud Build: submit a docker build, poll status, cancel."""

    def submit_build(
        self,
        credentials: Any,
        config: dict,
        *,
        context_object_uri: str,
        image_uri: str,
        build_sa: str | None,
        timeout: str,
    ) -> str:
        project_id = config["project_id"]
        bucket, obj = _parse_gs_uri(context_object_uri)
        build_url = f"{_CLOUD_BUILD_API}/projects/{project_id}/builds"
        build_body: dict = {
            "source": {
                "storageSource": {
                    "bucket": bucket,
                    "object": obj,
                }
            },
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": ["build", "-t", image_uri, "-f", "Dockerfile", "."],
                }
            ],
            "images": [image_uri],
            "options": {
                "machineType": "E2_HIGHCPU_8",
            },
            "timeout": timeout,
        }
        if build_sa and build_sa != "null":
            build_body["serviceAccount"] = f"projects/{project_id}/serviceAccounts/{build_sa}"
            build_body["options"]["defaultLogsBucketBehavior"] = "REGIONAL_USER_OWNED_BUCKET"
            logger.info("Cloud Build will run as SA: %s", build_sa)

        result = authorized_request(credentials, "POST", build_url, build_body)
        build_id = result.get("metadata", {}).get("build", {}).get("id", "")
        logger.info("Submitted Cloud Build %s for image %s", build_id, image_uri)
        return build_id

    def check_build_status(self, credentials: Any, config: dict, build_id: str) -> str:
        project_id = config["project_id"]
        url = f"{_CLOUD_BUILD_API}/projects/{project_id}/builds/{build_id}"
        try:
            result = authorized_request(credentials, "GET", url)
            return result.get("status", "UNKNOWN")
        except Exception as e:
            logger.error("Failed to check build %s: %s", build_id, e)
            return "UNKNOWN"

    def cancel_build(self, credentials: Any, config: dict, build_id: str) -> None:
        project_id = config["project_id"]
        url = f"{_CLOUD_BUILD_API}/projects/{project_id}/builds/{build_id}:cancel"
        try:
            authorized_request(credentials, "POST", url, {})
        except Exception as e:
            logger.warning("Cloud Build cancel API returned error (may already be done): %s", e)
