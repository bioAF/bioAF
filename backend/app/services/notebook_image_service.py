"""Notebook image build service.

Manages the Artifact Registry repository and Cloud Build jobs for the
bioaf-scrna notebook environment image. Uses REST APIs with google-auth
credentials to avoid additional package dependencies.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.credentials.credential_injector import load_gcp_credentials
from app.adapters.image_build import get_image_build_provider
from app.adapters.image_registry import get_image_registry_provider
from app.adapters.image_registry.gcp import AR_REPO_ID
from app.exceptions import StateError, ValidationError
from app.services.image_build_platform import ImagePlatform, resolve_image_credentials, resolve_image_platform

logger = logging.getLogger("bioaf.notebook_image")

IMAGE_NAME = "bioaf-scrna"
IMAGE_TAG = "latest"

# Dockerfile is embedded so it can be built from the running backend
# container without needing the source repo on disk.
DOCKERFILE_CONTENT = """\
# Pinned for reproducibility (postmortem of build 28b547ac: floating :latest left
# R 4.1.2 too old for current packages). Bump these ARGs deliberately; never float
# to :latest. After changing a pin, validate with a Cloud Build, then lock results.
FROM jupyter/scipy-notebook@sha256:fca4bcc9cbd49d9a15e0e4df6c666adf17776c950da9fa94a4f0a045d5c4ad33

USER root

# ---- Version pins (single source of truth) ----
ARG R_VERSION=4.4.3
ARG BIOC_VERSION=3.20
ARG CRAN_SNAPSHOT=2024-12-02
ARG PRESTO_REF=a24772a135c7895a8183b007376050556c60a05b
ARG RSTUDIO_DEB=rstudio-server-2024.04.2-764-amd64.deb

# System libraries. Several of these are the reason R/Python packages fail to
# build or load if absent: libglpk (igraph), libopenblas (igraph.so links
# libopenblas.so.0 at runtime; without it igraph fails to load and cascades to
# Seurat/scran/batchelor/clusterProfiler), libgsl, libgeos (spatial), cairo/xt/
# harfbuzz/fribidi (tidyverse graphics via ragg and textshaping), and HDF5 for
# .h5 / .h5ad I/O. The base image is Ubuntu 22.04 (jammy).
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libhdf5-dev libcurl4-openssl-dev libssl-dev libxml2-dev \\
    libglpk-dev libopenblas-dev libgsl-dev libgeos-dev libfftw3-dev cmake \\
    libcairo2-dev libxt-dev libfontconfig1-dev \\
    libharfbuzz-dev libfribidi-dev \\
    libpng-dev libtiff5-dev libjpeg-dev \\
    build-essential gfortran \\
    gdebi-core wget \\
    git openssh-client \\
    && rm -rf /var/lib/apt/lists/*

# Pinned R from Posit r-builds (exact, reproducible) instead of Ubuntu's stale
# r-base: jammy ships R 4.1.2 (2021), too old for current CRAN/Bioconductor
# packages (yulab.utils/ggfun/Matrix are 'not available' for it). r-builds keeps
# every version on its CDN, so R_VERSION is durably pinnable.
RUN wget -q https://cdn.posit.co/r/ubuntu-2204/pkgs/r-${R_VERSION}_1_amd64.deb \\
    && apt-get update \\
    && gdebi -n r-${R_VERSION}_1_amd64.deb \\
    && rm r-${R_VERSION}_1_amd64.deb \\
    && rm -rf /var/lib/apt/lists/* \\
    && ln -sf /opt/R/${R_VERSION}/bin/R /usr/local/bin/R \\
    && ln -sf /opt/R/${R_VERSION}/bin/Rscript /usr/local/bin/Rscript

# Python: single-cell, bulk RNA-seq, and general bioinformatics
RUN pip install --no-cache-dir \\
    scanpy==1.11.5 anndata==0.12.16 muon==0.1.7 scvi-tools==1.4.2 \\
    leidenalg==0.12.0 python-igraph==1.0.0 harmonypy==2.0.0 scanorama==1.7.4 bbknn==1.6.0 \\
    scrublet==0.2.3 doubletdetection==4.3.0.post1 celltypist==1.7.1 decoupler==2.1.6 gseapy==1.2.1 \\
    scikit-misc==0.5.2 statsmodels==0.14.6 scvelo==0.3.4 \\
    pandas==2.3.3 numpy==1.26.4 scipy==1.17.1 seaborn==0.13.2 plotly==6.8.0 \\
    umap-learn==0.5.12 pybiomart==0.2.0 biopython==1.87 pysam==0.24.0 anndata2ri==2.0 \\
    __STORAGE_PIP_PACKAGES__

# Install R packages as precompiled binaries from a DATED Posit P3M snapshot, so
# every CRAN version is frozen to that date (reproducible) and builds are fast
# (no source compilation, which is also what avoided the igraph/openblas link).
# Combined with the verification step below, silent package failures are caught.
RUN echo "options(repos = c(P3M = 'https://packagemanager.posit.co/cran/__linux__/jammy/${CRAN_SNAPSHOT}'))" >> /opt/R/${R_VERSION}/lib/R/etc/Rprofile.site
RUN echo 'options(HTTPUserAgent = sprintf("R/%s R (%s)", getRversion(), paste(getRversion(), R.version$platform, R.version$arch, R.version$os)))' >> /opt/R/${R_VERSION}/lib/R/etc/Rprofile.site

# R / CRAN: Seurat stack, single-cell helpers, plotting, dev tooling
RUN R -e "install.packages(c('Seurat','SeuratObject','Matrix','harmony','future','tidyverse','data.table','patchwork','cowplot','ggplot2','pheatmap','RColorBrewer','viridis','devtools','remotes','R.utils','BiocManager'))"

# hdf5r built from source links whatever HDF5 its configure finds first, which in
# this base image is conda's (libhdf5_hl.so.310) -- not on the runtime linker path,
# so the package fails to load. Force it against the system HDF5 from libhdf5-dev
# (libhdf5_hl.so.*, on the standard path) via the system h5cc wrapper.
RUN R -e "install.packages('hdf5r', configure.args='--with-hdf5=/usr/bin/h5cc')"

# presto (fast Wilcoxon for Seurat FindMarkers) ships only from GitHub; pinned to a commit
RUN R -e "remotes::install_github('immunogenomics/presto', ref='${PRESTO_REF}', upgrade='never')"

# R / Bioconductor: pinned release, single-cell, bulk DE, enrichment, annotation
RUN R -e "BiocManager::install(version='${BIOC_VERSION}', update=FALSE, ask=FALSE); BiocManager::install(c('SingleCellExperiment','scater','scran','scuttle','glmGamPoi','batchelor','DropletUtils','SingleR','celldex','zellkonverter','DESeq2','edgeR','limma','clusterProfiler','fgsea','ComplexHeatmap','EnhancedVolcano','org.Hs.eg.db','org.Mm.eg.db','AnnotationDbi'), update=FALSE, ask=FALSE)"

# Fail the build if any expected package is missing. install.packages() and
# BiocManager::install() exit 0 even when a package fails to install, which is
# how images previously shipped without Seurat. This is the guardrail.
RUN R -e "req <- c('Seurat','SeuratObject','hdf5r','harmony','presto','SingleCellExperiment','scater','scran','glmGamPoi','batchelor','DropletUtils','SingleR','zellkonverter','DESeq2','edgeR','limma','clusterProfiler','fgsea','ComplexHeatmap','org.Hs.eg.db'); missing <- req[!req %in% rownames(installed.packages())]; if (length(missing) > 0) stop(paste('Missing R packages:', paste(missing, collapse=', '))); cat('R package check passed')"

# Verify the key Python imports resolve too
RUN python -c "import scanpy, anndata, scvi, muon, harmonypy, scanorama, scrublet, celltypist, decoupler, gseapy, scvelo; print('Python deps ok')"

# RStudio Server (pinned jammy build)
RUN apt-get update && apt-get install -y --no-install-recommends gdebi-core wget \\
    && wget -q https://download2.rstudio.org/server/jammy/amd64/${RSTUDIO_DEB} \\
    && gdebi -n ${RSTUDIO_DEB} \\
    && rm rstudio-server-*.deb \\
    && rm -rf /var/lib/apt/lists/*

USER ${NB_UID}

WORKDIR /home/jovyan
"""


def _render_dockerfile() -> str:
    """Fill the storage-client pip packages from the cloud-selected adapter.

    Keeps the cloud-specific storage dependency out of the service-layer template;
    the storage backend owns which client libraries a built image needs.
    """
    from app.adapters.registry import get_storage_adapter

    return DOCKERFILE_CONTENT.replace("__STORAGE_PIP_PACKAGES__", get_storage_adapter().image_storage_pip_packages())


def get_image_uri(config: dict) -> str:
    """Construct the full image URI via the cloud-selected image registry.

    ``config`` is the cloud-resolved provider config (``ImagePlatform.config``):
    project_id+region on GCP, account_id+region on AWS.
    """
    return get_image_registry_provider().image_uri(config, IMAGE_NAME, IMAGE_TAG)


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


async def _read_config(session: AsyncSession, key: str) -> str:
    """Read a single platform_config value."""
    from app.platform.platform_config_service import PlatformConfigService

    val = await PlatformConfigService.get(session, key)
    return val if val is not None else "null"


async def _set_config(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a platform_config key."""
    from app.platform.platform_config_service import PlatformConfigService

    await PlatformConfigService.set(session, key, value)


async def ensure_image_repository(session: AsyncSession, platform: ImagePlatform) -> str:
    """Create the image repository if it does not exist (idempotent).

    Returns the repository resource name. Delegates the cloud-specific repo-ensure
    to the image-registry provider (GCP: a shared Artifact Registry repo; AWS: a
    per-image ECR repository named ``bioaf-scrna``).
    """
    credentials = await resolve_image_credentials(session, platform)
    return get_image_registry_provider().ensure_repository(credentials, platform.config, IMAGE_NAME)


async def ensure_artifact_registry(session: AsyncSession, project_id: str, region: str) -> str:
    """Ensure the shared Artifact Registry repo (GCP-only; environment builds).

    Retained for ``environment_build_service`` (the work-node / conda Packer image
    build, a GCP-only residual island whose AWS analog is an AMI). The cloud-neutral
    notebook/cellxgene path uses :func:`ensure_image_repository` instead.
    """
    credentials = await _get_credentials(session)
    return get_image_registry_provider().ensure_repository(
        credentials, {"project_id": project_id, "region": region}, IMAGE_NAME
    )


async def _upload_build_context(session: AsyncSession, working_bucket: str) -> str:
    """Create a tar.gz with the Dockerfile and upload to the working bucket.

    Returns the storage object path (bucket-relative).
    """
    from app.adapters.registry import get_storage_adapter

    # Create tar.gz in memory with the Dockerfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        dockerfile_bytes = _render_dockerfile().encode()
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(dockerfile_bytes)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(dockerfile_bytes))

    buf.seek(0)
    object_path = "builds/bioaf-scrna/source.tar.gz"
    adapter = get_storage_adapter()
    await adapter.upload_file(adapter.build_uri(working_bucket, object_path), buf, content_type="application/gzip")
    logger.info("Uploaded build context to the %s bucket at %s", working_bucket, object_path)

    return object_path


async def submit_image_build(session: AsyncSession, platform: ImagePlatform) -> str:
    """Submit a build job to build and push the bioaf-scrna image.

    Returns the backend build ID (Cloud Build id on GCP, CodeBuild id on AWS).
    """
    working_bucket = await _read_config(session, "working_bucket_name")
    if not working_bucket or working_bucket == "null":
        raise ValidationError("Working bucket not configured. Deploy storage first.")

    # Upload Dockerfile as build context
    from app.adapters.registry import get_storage_adapter

    object_path = await _upload_build_context(session, working_bucket)
    context_uri = get_storage_adapter().build_uri(working_bucket, object_path)

    image_uri = get_image_uri(platform.config)
    credentials = await resolve_image_credentials(session, platform)

    # On GCP, fall back to the credentials' own SA when no explicit build SA is
    # configured (matches the pre-6e behavior). AWS has no build SA.
    build_sa = platform.build_sa
    if platform.cloud_provider != "aws" and not build_sa:
        build_sa = getattr(credentials, "service_account_email", None)

    # Submit the build via the cloud-selected image-build provider.
    build_id = get_image_build_provider().submit_build(
        credentials,
        platform.config,
        context_object_uri=context_uri,
        image_uri=image_uri,
        build_sa=build_sa,
        timeout="7200s",
    )

    # Store build ID for monitoring
    await _set_config(session, "notebook_image_build_id", build_id)
    await _set_config(session, "notebook_image_build_status", "WORKING")

    return build_id


async def check_build_status(session: AsyncSession, build_id: str) -> str:
    """Check the status of an image build.

    Returns one of: QUEUED, WORKING, SUCCESS, FAILURE, CANCELLED, TIMEOUT (or
    UNKNOWN). Delegates the cloud-specific status read to the image-build provider.
    """
    platform = await resolve_image_platform(session)
    credentials = await resolve_image_credentials(session, platform)
    return get_image_build_provider().check_build_status(credentials, platform.config, build_id)


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

    platform = await resolve_image_platform(session)
    platform.require_target()
    credentials = await resolve_image_credentials(session, platform)
    get_image_build_provider().cancel_build(credentials, platform.config, build_id)

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
    """Full flow: ensure the image repo, submit build, store image URI on success.

    Called when a notebook component (rstudio/jupyterhub) is enabled.
    The image URI is NOT written until the build succeeds (via poll_image_build).
    Returns the build ID.
    """
    platform = await resolve_image_platform(session)
    platform.require_target()
    platform.require_build_service()

    # Clear any stale image URI from a previous failed build attempt
    await _set_config(session, "bioaf_scrna_image", "null")
    # Reset build tracking so poll_image_build picks up the new build
    await _set_config(session, "notebook_image_build_status", "null")
    await _set_config(session, "notebook_image_build_id", "null")
    # Store the AR repo path (GCP-only; the ECR URI is derived per-image). Nothing
    # in the backend reads this key, but it is kept for GCP byte-identity.
    if platform.cloud_provider != "aws":
        await _set_config(
            session,
            "artifact_registry_repo",
            f"{platform.config['region']}-docker.pkg.dev/{platform.config['project_id']}/{AR_REPO_ID}",
        )

    # Create the image repository (idempotent)
    await ensure_image_repository(session, platform)

    # Submit build
    build_id = await submit_image_build(session, platform)

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
        definition_content=_render_dockerfile(),
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

    platform = await resolve_image_platform(session)
    if not platform.has_target:
        return None

    status = await check_build_status(session, build_id)
    await _set_config(session, "notebook_image_build_status", status)

    if status == "SUCCESS":
        logger.info("Notebook image build %s completed successfully", build_id)
        # Now that the build succeeded, write the image URI
        image_uri = get_image_uri(platform.config)
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
