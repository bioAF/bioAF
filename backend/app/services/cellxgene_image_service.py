"""Cellxgene image build service.

Manages the Cloud Build job for the cellxgene container image. Follows the
same pattern as notebook_image_service: embedded Dockerfile, GCS context
upload, Cloud Build submission, and polling.
"""

from __future__ import annotations

import io
import json
import logging
import tarfile
import time

import google.auth
import google.auth.transport.requests
from google.cloud import storage
from google.oauth2 import service_account
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bioaf.cellxgene_image")

AR_REPO_ID = "bioaf-images"
IMAGE_NAME = "bioaf-cellxgene"
IMAGE_TAG = "latest"

DOCKERFILE_CONTENT = """\
FROM python:3.11-slim

RUN pip install --no-cache-dir cellxgene gcsfs

EXPOSE 5005

ENTRYPOINT ["cellxgene"]
"""


def get_image_uri(project_id: str, region: str) -> str:
    """Construct the full Artifact Registry image URI."""
    return f"{region}-docker.pkg.dev/{project_id}/{AR_REPO_ID}/{IMAGE_NAME}:{IMAGE_TAG}"


async def _get_credentials(session: AsyncSession):
    """Load GCP credentials from platform_config."""
    result = await session.execute(
        text("SELECT key, value FROM platform_config WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')")
    )
    config = {r[0]: r[1] for r in result.fetchall()}

    if config.get("gcp_credential_source") != "service_account_key":
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return creds

    key_json = config.get("gcp_service_account_key")
    if not key_json or key_json == "null":
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return creds

    key_data = json.loads(key_json)
    return service_account.Credentials.from_service_account_info(
        key_data,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _authorized_request(credentials, method: str, url: str, body: dict | None = None) -> dict:
    """Make an authenticated HTTP request to a GCP REST API."""
    import urllib.request

    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    headers = {
        "Authorization": f"Bearer {credentials.token}",
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


async def _read_config(session: AsyncSession, key: str) -> str:
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).fetchone()
    return row[0] if row else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ).bindparams(k=key, v=value)
    )


async def _upload_build_context(session: AsyncSession, project_id: str, working_bucket: str) -> str:
    """Create a tar.gz with the Dockerfile and upload to GCS."""
    credentials = await _get_credentials(session)
    client = storage.Client(project=project_id, credentials=credentials)
    bucket = client.bucket(working_bucket)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        dockerfile_bytes = DOCKERFILE_CONTENT.encode()
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_bytes)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(dockerfile_bytes))

    buf.seek(0)
    object_path = "builds/bioaf-cellxgene/source.tar.gz"
    blob = bucket.blob(object_path)
    blob.upload_from_file(buf, content_type="application/gzip")
    logger.info("Uploaded build context to gs://%s/%s", working_bucket, object_path)

    return object_path


async def submit_image_build(session: AsyncSession, project_id: str, region: str) -> str:
    """Submit a Cloud Build job for the cellxgene image. Returns the build ID."""
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValueError("Working bucket not configured. Deploy storage first.")

    object_path = await _upload_build_context(session, project_id, working_bucket)

    image_uri = get_image_uri(project_id, region)
    credentials = await _get_credentials(session)

    sa_email = await _read_config(session, "gcp_service_account_email")
    if not sa_email or sa_email == "null":
        sa_email = getattr(credentials, "service_account_email", None)

    build_url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds"
    build_body: dict = {
        "source": {
            "storageSource": {
                "bucket": working_bucket,
                "object": object_path,
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
        "timeout": "3600s",
    }
    if sa_email and sa_email != "null":
        build_body["serviceAccount"] = f"projects/{project_id}/serviceAccounts/{sa_email}"
        build_body["options"]["defaultLogsBucketBehavior"] = "REGIONAL_USER_OWNED_BUCKET"
        logger.info("Cloud Build will run as SA: %s", sa_email)

    result = _authorized_request(credentials, "POST", build_url, build_body)
    build_id = result.get("metadata", {}).get("build", {}).get("id", "")
    logger.info("Submitted Cloud Build %s for image %s", build_id, image_uri)

    await _set_config(session, "cellxgene_image_build_id", build_id)
    await _set_config(session, "cellxgene_image_build_status", "WORKING")

    return build_id


async def check_build_status(session: AsyncSession, project_id: str, build_id: str) -> str:
    """Check the status of a Cloud Build job."""
    credentials = await _get_credentials(session)
    url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds/{build_id}"

    try:
        result = _authorized_request(credentials, "GET", url)
        return result.get("status", "UNKNOWN")
    except Exception as e:
        logger.error("Failed to check build %s: %s", build_id, e)
        return "UNKNOWN"


async def build_cellxgene_image(session: AsyncSession) -> str:
    """Full flow: ensure AR repo exists, submit build, return build ID.

    Called when the cellxgene component is enabled. The image URI is NOT
    written until the build succeeds (via poll_image_build).
    """
    from app.services.notebook_image_service import ensure_artifact_registry

    project_id = await _read_config(session, "gcp_project_id")
    region = await _read_config(session, "gcp_region")

    if not project_id or project_id == "null":
        raise ValueError("GCP project not configured")
    if not region or region == "null":
        raise ValueError("GCP region not configured")

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
