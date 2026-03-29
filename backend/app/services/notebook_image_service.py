"""Notebook image build service.

Manages the Artifact Registry repository and Cloud Build jobs for the
bioaf-scrna notebook environment image. Uses REST APIs with google-auth
credentials to avoid additional package dependencies.
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

logger = logging.getLogger("bioaf.notebook_image")

AR_REPO_ID = "bioaf-images"
IMAGE_NAME = "bioaf-scrna"
IMAGE_TAG = "latest"

# Dockerfile is embedded so it can be built from the running backend
# container without needing the source repo on disk.
DOCKERFILE_CONTENT = """\
FROM jupyter/scipy-notebook:latest

USER root

# System dependencies for R, HDF5, and git
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libhdf5-dev libcurl4-openssl-dev libssl-dev libxml2-dev \\
    r-base r-base-dev \\
    git openssh-client \\
    && rm -rf /var/lib/apt/lists/*

# Python scRNA-seq packages
RUN pip install --no-cache-dir \\
    scanpy anndata scvi-tools leidenalg \\
    pandas numpy matplotlib seaborn plotly \\
    umap-learn bbknn scrublet \\
    google-cloud-storage

# R packages (core set for Seurat and Bioconductor)
RUN R -e "install.packages(c('Seurat', 'ggplot2', 'tidyverse', 'pheatmap', 'devtools'), repos='https://cloud.r-project.org')"
RUN R -e "if (!requireNamespace('BiocManager', quietly=TRUE)) install.packages('BiocManager', repos='https://cloud.r-project.org'); BiocManager::install(c('SingleCellExperiment', 'scater', 'scran'))"

# RStudio Server
RUN apt-get update && apt-get install -y --no-install-recommends gdebi-core wget \\
    && wget -q https://download2.rstudio.org/server/jammy/amd64/rstudio-server-2024.04.2-764-amd64.deb \\
    && gdebi -n rstudio-server-2024.04.2-764-amd64.deb \\
    && rm rstudio-server-*.deb \\
    && rm -rf /var/lib/apt/lists/*

# gsutil for GCS home directory sync
RUN pip install --no-cache-dir gsutil

USER ${NB_UID}

WORKDIR /home/jovyan
"""


def get_image_uri(project_id: str, region: str) -> str:
    """Construct the full Artifact Registry image URI."""
    return f"{region}-docker.pkg.dev/{project_id}/{AR_REPO_ID}/{IMAGE_NAME}:{IMAGE_TAG}"


async def _get_credentials(session: AsyncSession):
    """Load GCP credentials from platform_config (Pattern 1)."""
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
    """Read a single platform_config value."""
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = :k").bindparams(k=key))).fetchone()
    return row[0] if row else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a platform_config key."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ).bindparams(k=key, v=value)
    )


async def ensure_artifact_registry(session: AsyncSession, project_id: str, region: str) -> str:
    """Create the Artifact Registry Docker repo if it does not exist.

    Returns the full repository name.
    """
    credentials = await _get_credentials(session)
    parent = f"projects/{project_id}/locations/{region}"
    repo_name = f"{parent}/repositories/{AR_REPO_ID}"

    # Check if repo exists
    url = f"https://artifactregistry.googleapis.com/v1/{repo_name}"
    try:
        _authorized_request(credentials, "GET", url)
        logger.info("Artifact Registry repo %s already exists", repo_name)
        return repo_name
    except Exception:
        pass  # 404 expected, create it

    # Create repo
    create_url = f"https://artifactregistry.googleapis.com/v1/{parent}/repositories?repositoryId={AR_REPO_ID}"
    body = {
        "format": "DOCKER",
        "description": "bioAF container images for notebook environments",
    }
    try:
        _authorized_request(credentials, "POST", create_url, body)
        logger.info("Created Artifact Registry repo %s", repo_name)
    except Exception as e:
        # May be ALREADY_EXISTS race or permission error
        logger.warning("Artifact Registry create returned error (may already exist): %s", e)

    return repo_name


async def _upload_build_context(session: AsyncSession, project_id: str, working_bucket: str) -> str:
    """Create a tar.gz with the Dockerfile and upload to GCS.

    Returns the GCS object path (bucket-relative).
    """
    credentials = await _get_credentials(session)
    client = storage.Client(project=project_id, credentials=credentials)
    bucket = client.bucket(working_bucket)

    # Create tar.gz in memory with the Dockerfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        dockerfile_bytes = DOCKERFILE_CONTENT.encode()
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_bytes)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(dockerfile_bytes))

    buf.seek(0)
    object_path = "builds/bioaf-scrna/source.tar.gz"
    blob = bucket.blob(object_path)
    blob.upload_from_file(buf, content_type="application/gzip")
    logger.info("Uploaded build context to gs://%s/%s", working_bucket, object_path)

    return object_path


async def submit_image_build(session: AsyncSession, project_id: str, region: str) -> str:
    """Submit a Cloud Build job to build and push the bioaf-scrna image.

    Returns the Cloud Build operation/build ID.
    """
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValueError("Working bucket not configured. Deploy storage first.")

    # Upload Dockerfile as build context
    object_path = await _upload_build_context(session, project_id, working_bucket)

    image_uri = get_image_uri(project_id, region)
    credentials = await _get_credentials(session)

    # Resolve the platform SA email for Cloud Build to use.
    # Priority: gcp_service_account_email from config, then credentials object.
    sa_email = await _read_config(session, "gcp_service_account_email")
    if not sa_email or sa_email == "null":
        sa_email = getattr(credentials, "service_account_email", None)

    # Submit Cloud Build
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
        "timeout": "7200s",
    }
    if sa_email and sa_email != "null":
        build_body["serviceAccount"] = f"projects/{project_id}/serviceAccounts/{sa_email}"
        build_body["options"]["defaultLogsBucketBehavior"] = "REGIONAL_USER_OWNED_BUCKET"
        logger.info("Cloud Build will run as SA: %s", sa_email)

    result = _authorized_request(credentials, "POST", build_url, build_body)
    build_id = result.get("metadata", {}).get("build", {}).get("id", "")
    logger.info("Submitted Cloud Build %s for image %s", build_id, image_uri)

    # Store build ID for monitoring
    await _set_config(session, "notebook_image_build_id", build_id)
    await _set_config(session, "notebook_image_build_status", "WORKING")

    return build_id


async def check_build_status(session: AsyncSession, project_id: str, build_id: str) -> str:
    """Check the status of a Cloud Build job.

    Returns one of: QUEUED, WORKING, SUCCESS, FAILURE, CANCELLED, TIMEOUT.
    """
    credentials = await _get_credentials(session)
    url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds/{build_id}"

    try:
        result = _authorized_request(credentials, "GET", url)
        return result.get("status", "UNKNOWN")
    except Exception as e:
        logger.error("Failed to check build %s: %s", build_id, e)
        return "UNKNOWN"


async def cancel_build(session: AsyncSession) -> str:
    """Cancel the active Cloud Build job.

    Returns the build ID that was cancelled.
    Raises ValueError if there is no active build to cancel.
    """
    build_id = await _read_config(session, "notebook_image_build_id")
    if not build_id or build_id == "null":
        raise ValueError("No active build to cancel.")

    current_status = await _read_config(session, "notebook_image_build_status")
    if current_status in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
        raise ValueError(f"Build already finished with status {current_status}.")

    project_id = await _read_config(session, "gcp_project_id")
    if not project_id or project_id == "null":
        raise ValueError("GCP project not configured.")

    credentials = await _get_credentials(session)
    url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds/{build_id}:cancel"
    try:
        _authorized_request(credentials, "POST", url, {})
    except Exception as e:
        logger.warning("Cloud Build cancel API returned error (may already be done): %s", e)

    await _set_config(session, "notebook_image_build_status", "CANCELLED")
    # Mark notebook components back to build_failed so user can retry
    await session.execute(
        text("""
        UPDATE component_states SET status = 'build_failed'
        WHERE component_key IN ('rstudio', 'jupyterhub')
        AND enabled = true AND status = 'provisioning'
        """)
    )
    await session.flush()

    return build_id


async def build_notebook_image(session: AsyncSession) -> str:
    """Full flow: ensure AR repo, submit build, store image URI on success.

    Called when a notebook component (rstudio/jupyterhub) is enabled.
    The image URI is NOT written until the build succeeds (via poll_image_build).
    Returns the build ID.
    """
    project_id = await _read_config(session, "gcp_project_id")
    region = await _read_config(session, "gcp_region")

    if not project_id or project_id == "null":
        raise ValueError("GCP project not configured")
    if not region or region == "null":
        raise ValueError("GCP region not configured")

    # Clear any stale image URI from a previous failed build attempt
    await _set_config(session, "bioaf_scrna_image", "null")
    # Reset build tracking so poll_image_build picks up the new build
    await _set_config(session, "notebook_image_build_status", "null")
    await _set_config(session, "notebook_image_build_id", "null")
    # Store the AR repo path (but NOT the image URI -- that is set by
    # poll_image_build only after the build succeeds)
    await _set_config(session, "artifact_registry_repo", f"{region}-docker.pkg.dev/{project_id}/{AR_REPO_ID}")

    # Create AR repo (idempotent)
    await ensure_artifact_registry(session, project_id, region)

    # Submit build
    build_id = await submit_image_build(session, project_id, region)

    return build_id


async def poll_image_build(session: AsyncSession) -> str | None:
    """Check if there is an active image build and update its status.

    Called by the background task loop. Returns the current status
    or None if no active build.
    """
    build_id = await _read_config(session, "notebook_image_build_id")
    if not build_id or build_id == "null":
        return None

    current_status = await _read_config(session, "notebook_image_build_status")
    if current_status in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
        return current_status

    project_id = await _read_config(session, "gcp_project_id")
    if not project_id or project_id == "null":
        return None

    status = await check_build_status(session, project_id, build_id)
    await _set_config(session, "notebook_image_build_status", status)

    if status == "SUCCESS":
        logger.info("Notebook image build %s completed successfully", build_id)
        # Now that the build succeeded, write the image URI
        region = await _read_config(session, "gcp_region")
        image_uri = get_image_uri(project_id, region)
        await _set_config(session, "bioaf_scrna_image", image_uri)
        # Update component states for notebook components
        await session.execute(
            text("""
            UPDATE component_states SET status = 'enabled'
            WHERE component_key IN ('rstudio', 'jupyterhub')
            AND enabled = true AND status = 'provisioning'
            """)
        )
    elif status in ("FAILURE", "CANCELLED", "TIMEOUT"):
        logger.error("Notebook image build %s failed with status %s", build_id, status)
        # Clear the image URI since the build failed
        await _set_config(session, "bioaf_scrna_image", "null")
        # Mark notebook components as build_failed so the UI shows retry
        await session.execute(
            text("""
            UPDATE component_states SET status = 'build_failed'
            WHERE component_key IN ('rstudio', 'jupyterhub')
            AND enabled = true AND status = 'provisioning'
            """)
        )

    await session.flush()
    return status
