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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StateError, ValidationError
from app.platform.credential_injector import load_gcp_credentials

logger = logging.getLogger("bioaf.notebook_image")

AR_REPO_ID = "bioaf-images"
IMAGE_NAME = "bioaf-scrna"
IMAGE_TAG = "latest"

# Dockerfile is embedded so it can be built from the running backend
# container without needing the source repo on disk.
DOCKERFILE_CONTENT = """\
FROM jupyter/scipy-notebook:latest

USER root

# System libraries. Several of these are the reason R/Python packages fail to
# build if absent: libglpk (igraph), libgsl, libgeos (spatial), cairo/xt/
# harfbuzz/fribidi (tidyverse graphics via ragg and textshaping), and HDF5 for
# .h5 / .h5ad I/O. The base image is Ubuntu 22.04 (jammy).
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libhdf5-dev libcurl4-openssl-dev libssl-dev libxml2-dev \\
    libglpk-dev libgsl-dev libgeos-dev libfftw3-dev cmake \\
    libcairo2-dev libxt-dev libfontconfig1-dev \\
    libharfbuzz-dev libfribidi-dev \\
    libpng-dev libtiff5-dev libjpeg-dev \\
    r-base r-base-dev \\
    git openssh-client \\
    && rm -rf /var/lib/apt/lists/*

# Python: single-cell, bulk RNA-seq, and general bioinformatics
RUN pip install --no-cache-dir \\
    scanpy anndata muon scvi-tools \\
    leidenalg python-igraph harmonypy scanorama bbknn \\
    scrublet doubletdetection celltypist decoupler gseapy \\
    scikit-misc statsmodels scvelo \\
    pandas numpy scipy matplotlib seaborn plotly \\
    umap-learn pybiomart biopython pysam anndata2ri \\
    google-cloud-storage gsutil

# Install R packages as precompiled binaries from the Posit Public Package
# Manager (P3M) rather than compiling from source. This turns a ~1h fragile
# build into a fast one; CRAN is kept as a source fallback. Combined with the
# verification step below, it makes silent package failures impossible.
RUN echo 'options(repos = c(P3M = "https://packagemanager.posit.co/cran/__linux__/jammy/latest", CRAN = "https://cloud.r-project.org"))' >> /usr/lib/R/etc/Rprofile.site
RUN echo 'options(HTTPUserAgent = sprintf("R/%s R (%s)", getRversion(), paste(getRversion(), R.version$platform, R.version$arch, R.version$os)))' >> /usr/lib/R/etc/Rprofile.site

# R / CRAN: Seurat stack, single-cell helpers, plotting, dev tooling
RUN R -e "install.packages(c('Seurat','SeuratObject','hdf5r','Matrix','harmony','future','tidyverse','data.table','patchwork','cowplot','ggplot2','pheatmap','RColorBrewer','viridis','devtools','remotes','R.utils','BiocManager'))"

# presto (fast Wilcoxon for Seurat FindMarkers) ships only from GitHub
RUN R -e "remotes::install_github('immunogenomics/presto', upgrade='never')"

# R / Bioconductor: single-cell, bulk DE, enrichment, annotation
RUN R -e "BiocManager::install(c('SingleCellExperiment','scater','scran','scuttle','glmGamPoi','batchelor','DropletUtils','SingleR','celldex','zellkonverter','DESeq2','edgeR','limma','clusterProfiler','fgsea','ComplexHeatmap','EnhancedVolcano','org.Hs.eg.db','org.Mm.eg.db','AnnotationDbi'), update=FALSE, ask=FALSE)"

# Fail the build if any expected package is missing. install.packages() and
# BiocManager::install() exit 0 even when a package fails to install, which is
# how images previously shipped without Seurat. This is the guardrail.
RUN R -e "req <- c('Seurat','SeuratObject','hdf5r','harmony','presto','SingleCellExperiment','scater','scran','glmGamPoi','batchelor','DropletUtils','SingleR','zellkonverter','DESeq2','edgeR','limma','clusterProfiler','fgsea','ComplexHeatmap','org.Hs.eg.db'); missing <- req[!req %in% rownames(installed.packages())]; if (length(missing) > 0) stop(paste('Missing R packages:', paste(missing, collapse=', '))); cat('R package check passed')"

# Verify the key Python imports resolve too
RUN python -c "import scanpy, anndata, scvi, muon, harmonypy, scanorama, scrublet, celltypist, decoupler, gseapy, scvelo; print('Python deps ok')"

# RStudio Server (jammy build, matching the base image)
RUN apt-get update && apt-get install -y --no-install-recommends gdebi-core wget \\
    && wget -q https://download2.rstudio.org/server/jammy/amd64/rstudio-server-2024.04.2-764-amd64.deb \\
    && gdebi -n rstudio-server-2024.04.2-764-amd64.deb \\
    && rm rstudio-server-*.deb \\
    && rm -rf /var/lib/apt/lists/*

USER ${NB_UID}

WORKDIR /home/jovyan
"""


def get_image_uri(project_id: str, region: str) -> str:
    """Construct the full Artifact Registry image URI."""
    return f"{region}-docker.pkg.dev/{project_id}/{AR_REPO_ID}/{IMAGE_NAME}:{IMAGE_TAG}"


async def _get_credentials(session: AsyncSession):
    """Load GCP credentials via the central credential_injector.

    Routes through `credential_injector.load_gcp_credentials` so that
    `gcp_bootstrap_sa_email` impersonation is honored (bioaf-bootstrap holds
    `roles/cloudbuild.builds.editor` and `roles/artifactregistry.admin`,
    which bioaf-app does not).
    """
    from app.platform.platform_config_service import PlatformConfigService

    config = await PlatformConfigService.get_many(
        session,
        [
            "gcp_credential_source",
            "gcp_service_account_key",
            "gcp_service_account_email",
            "gcp_bootstrap_sa_email",
        ],
    )

    # An explicit "null" string sentinel from earlier code paths means
    # "no key set"; let the injector see it as absent.
    if config.get("gcp_service_account_key") in ("null", None, ""):
        config.pop("gcp_service_account_key", None)
        # Without a key, fall back to ADC even if the source claims keys.
        if config.get("gcp_credential_source") == "service_account_key":
            config["gcp_credential_source"] = "vm_default"

    return load_gcp_credentials(config)


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
    from app.platform.platform_config_service import PlatformConfigService

    val = await PlatformConfigService.get(session, key)
    return val if val is not None else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a platform_config key."""
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, key, value)


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
    from app.adapters.registry import get_storage_adapter

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
    await get_storage_adapter().upload_file(
        f"gs://{working_bucket}/{object_path}", buf, content_type="application/gzip"
    )
    logger.info("Uploaded build context to gs://%s/%s", working_bucket, object_path)

    return object_path


async def submit_image_build(session: AsyncSession, project_id: str, region: str) -> str:
    """Submit a Cloud Build job to build and push the bioaf-scrna image.

    Returns the Cloud Build operation/build ID.
    """
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValidationError("Working bucket not configured. Deploy storage first.")

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
    Raises a DomainError if there is no active build to cancel.
    """
    build_id = await _read_config(session, "notebook_image_build_id")
    if not build_id or build_id == "null":
        raise ValidationError("No active build to cancel.")

    current_status = await _read_config(session, "notebook_image_build_status")
    if current_status in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
        raise StateError(f"Build already finished with status {current_status}.")

    project_id = await _read_config(session, "gcp_project_id")
    if not project_id or project_id == "null":
        raise ValidationError("GCP project not configured.")

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
        raise ValidationError("GCP project not configured")
    if not region or region == "null":
        raise ValidationError("GCP region not configured")

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


async def _ensure_default_environment(session: AsyncSession, image_uri: str) -> None:
    """Create the default scRNA-seq environment if none exists yet.

    Called after a successful notebook image build so users have an
    environment to select immediately.
    """
    from app.models.environment import Environment
    from app.models.environment_version import EnvironmentVersion

    # Get the org -- single-tenant, so take the first one
    row = (await session.execute(text("SELECT id FROM organizations LIMIT 1"))).fetchone()
    if not row:
        logger.warning("No organization found; skipping default environment creation")
        return
    org_id = row[0]

    # Get the first admin user to attribute creation to
    admin_row = (
        await session.execute(
            text(
                "SELECT u.id FROM users u "
                "JOIN roles r ON u.role_id = r.id "
                "WHERE u.organization_id = :org_id AND r.name = 'admin' "
                "ORDER BY u.id LIMIT 1"
            ).bindparams(org_id=org_id)
        )
    ).fetchone()
    if not admin_row:
        logger.warning("No admin user found; skipping default environment creation")
        return
    user_id = admin_row[0]

    # Skip if an environment already exists for this org
    existing = (
        await session.execute(
            text("SELECT id FROM environments WHERE organization_id = :org_id LIMIT 1").bindparams(org_id=org_id)
        )
    ).fetchone()
    if existing:
        logger.info("Environment already exists for org %d; skipping default creation", org_id)
        return

    env = Environment(
        name="bioAF scRNA-seq",
        description="Default single-cell RNA-seq environment with scanpy, Seurat, and RStudio",
        organization_id=org_id,
        created_by_user_id=user_id,
        visibility="team",
    )
    session.add(env)
    await session.flush()

    version = EnvironmentVersion(
        environment_id=env.id,
        version_number=1,
        status="ready",
        definition_format="dockerfile",
        definition_content=DOCKERFILE_CONTENT,
        image_uri=image_uri,
        created_by_user_id=user_id,
    )
    session.add(version)
    await session.flush()

    logger.info("Created default environment '%s' (id=%d) with image %s", env.name, env.id, image_uri)


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
        # Drain the wizard's queue: any rstudio/jupyterhub queued before the
        # image existed can now flip to enabled (or stay provisioning if
        # compute is not up yet). Local import to avoid a cycle through
        # component_queue -> notebook_image_service.build_notebook_image.
        from app.services.component_queue import process_queued_components

        await process_queued_components(session)
        # Update component states for notebook components
        await session.execute(
            text("""
            UPDATE component_states SET status = 'enabled'
            WHERE component_key IN ('rstudio', 'jupyterhub')
            AND enabled = true AND status = 'provisioning'
            """)
        )
        # Create default environment if none exists yet
        await _ensure_default_environment(session, image_uri)
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
