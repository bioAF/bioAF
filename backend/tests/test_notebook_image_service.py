"""Tests for notebook image build service."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from unittest.mock import patch

from app.services.notebook_image_service import (
    build_notebook_image,
    get_image_uri,
    poll_image_build,
    DOCKERFILE_CONTENT,
)


def test_get_image_uri():
    """Image URI follows Artifact Registry convention."""
    uri = get_image_uri("my-project", "us-central1")
    assert uri == "us-central1-docker.pkg.dev/my-project/bioaf-images/bioaf-scrna:latest"


def test_dockerfile_content_not_empty():
    """Embedded Dockerfile content matches expected structure."""
    assert "FROM jupyter/scipy-notebook" in DOCKERFILE_CONTENT
    assert "scanpy" in DOCKERFILE_CONTENT
    assert "rstudio-server" in DOCKERFILE_CONTENT
    assert "Seurat" in DOCKERFILE_CONTENT


def test_dockerfile_includes_single_cell_python_stack():
    """The bioinformatics Python stack a comp-bio needs is present."""
    for pkg in ("anndata", "scvi-tools", "harmonypy", "scrublet", "celltypist", "decoupler", "muon"):
        assert pkg in DOCKERFILE_CONTENT, f"missing Python package: {pkg}"


def test_dockerfile_includes_bulk_and_bioconductor_stack():
    """Bulk RNA-seq DE and core Bioconductor packages are present."""
    for pkg in ("DESeq2", "edgeR", "limma", "clusterProfiler", "SingleCellExperiment", "scran", "SingleR"):
        assert pkg in DOCKERFILE_CONTENT, f"missing Bioconductor/bulk package: {pkg}"


def test_dockerfile_includes_seurat_companions():
    """R packages that scRNA tutorials reach for beyond bare Seurat."""
    for pkg in ("hdf5r", "harmony", "presto"):
        assert pkg in DOCKERFILE_CONTENT, f"missing R companion package: {pkg}"


def test_dockerfile_uses_binary_package_repo():
    """R packages install from the Posit P3M binary repo for fast, reliable builds."""
    assert "packagemanager.posit.co" in DOCKERFILE_CONTENT
    assert "Rprofile.site" in DOCKERFILE_CONTENT


def test_dockerfile_fails_build_on_missing_r_packages():
    """A verification step turns silent install.packages() failures into build failures."""
    assert "installed.packages()" in DOCKERFILE_CONTENT
    assert "Missing R packages" in DOCKERFILE_CONTENT


def test_dockerfile_installs_openblas_runtime():
    """igraph's compiled .so links against libopenblas.so.0. Without the OpenBLAS
    runtime it fails to load, which cascades to Seurat, scran, batchelor, ggraph,
    clusterProfiler, fgsea, ComplexHeatmap (all igraph-dependent) and fails the build
    at the R package verification step. The apt step must install OpenBLAS."""
    assert "libopenblas" in DOCKERFILE_CONTENT


# The image must be reproducible: every input is pinned, nothing floats to :latest.
# An unpinned :latest is what broke build 28b547ac (upstream drift left R 4.1.2 too
# old for current packages). See ADR-066-era notebook-image hardening.
def test_dockerfile_pins_base_image_by_digest():
    assert "FROM jupyter/scipy-notebook@sha256:" in DOCKERFILE_CONTENT
    assert "scipy-notebook:latest" not in DOCKERFILE_CONTENT


def test_dockerfile_pins_modern_r_from_rbuilds():
    # Ubuntu jammy ships R 4.1.2 (2021), too old for current CRAN/Bioconductor.
    # Install a pinned modern R from Posit r-builds instead.
    assert "cdn.posit.co/r/ubuntu-2204/pkgs/r-" in DOCKERFILE_CONTENT
    assert "ARG R_VERSION=" in DOCKERFILE_CONTENT
    assert "r-base r-base-dev" not in DOCKERFILE_CONTENT


def test_dockerfile_pins_cran_to_dated_snapshot():
    assert "ARG CRAN_SNAPSHOT=" in DOCKERFILE_CONTENT
    assert "jammy/${CRAN_SNAPSHOT}" in DOCKERFILE_CONTENT
    assert "jammy/latest" not in DOCKERFILE_CONTENT


def test_dockerfile_pins_bioconductor_release():
    assert "ARG BIOC_VERSION=" in DOCKERFILE_CONTENT
    assert "BiocManager::install(version=" in DOCKERFILE_CONTENT


def test_dockerfile_pins_presto_commit():
    assert "ARG PRESTO_REF=" in DOCKERFILE_CONTENT
    assert "ref='${PRESTO_REF}'" in DOCKERFILE_CONTENT


def test_dockerfile_pins_python_package_versions():
    assert "scanpy==" in DOCKERFILE_CONTENT
    assert "scvi-tools==" in DOCKERFILE_CONTENT


@pytest_asyncio.fixture
async def seed_build_config(session):
    """Seed platform_config with build-related keys."""
    for key, value in [
        ("gcp_project_id", "test-project"),
        ("gcp_region", "us-central1"),
        ("gcp_credential_source", "vm_default"),
        ("working_bucket_name", "bioaf-working-abc123"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()


@pytest.mark.asyncio
async def test_poll_image_build_no_active_build(session):
    """poll_image_build returns None when no build is active."""
    result = await poll_image_build(session)
    assert result is None


@pytest.mark.asyncio
async def test_poll_image_build_already_complete(session):
    """poll_image_build returns cached status for completed builds."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "notebook_image_build_id", "v": "build-123"},
    )
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "notebook_image_build_status", "v": "SUCCESS"},
    )
    await session.commit()

    result = await poll_image_build(session)
    assert result == "SUCCESS"


@pytest.mark.asyncio
async def test_poll_image_build_clears_image_on_failure(session, seed_build_config):
    """poll_image_build clears bioaf_scrna_image when build fails."""
    for key, value in [
        ("notebook_image_build_id", "build-456"),
        ("notebook_image_build_status", "WORKING"),
        ("bioaf_scrna_image", "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    with patch(
        "app.services.notebook_image_service.check_build_status",
        return_value="FAILURE",
    ):
        result = await poll_image_build(session)

    assert result == "FAILURE"

    # Image URI should be cleared
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'"))).fetchone()
    assert row is not None
    assert row[0] == "null"


@pytest.mark.asyncio
async def test_build_notebook_image_clears_stale_uri(session, seed_build_config):
    """build_notebook_image clears any stale image URI before submitting a build."""
    # Seed a stale image URI from a previous failed attempt
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "bioaf_scrna_image", "v": "us-central1-docker.pkg.dev/old/repo/bioaf-scrna:latest"},
    )
    await session.commit()

    with (
        patch(
            "app.services.notebook_image_service.ensure_artifact_registry",
            return_value="projects/test-project/locations/us-central1/repositories/bioaf-images",
        ),
        patch(
            "app.services.notebook_image_service.submit_image_build",
            return_value="new-build-id",
        ),
    ):
        build_id = await build_notebook_image(session)

    assert build_id == "new-build-id"

    # The stale image URI must be cleared (set to "null")
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'"))).fetchone()
    assert row is not None
    assert row[0] == "null"


@pytest.mark.asyncio
async def test_build_notebook_image_does_not_write_image_uri(session, seed_build_config):
    """build_notebook_image must NOT write the final image URI; only poll does that."""
    with (
        patch(
            "app.services.notebook_image_service.ensure_artifact_registry",
            return_value="projects/test-project/locations/us-central1/repositories/bioaf-images",
        ),
        patch(
            "app.services.notebook_image_service.submit_image_build",
            return_value="build-789",
        ),
    ):
        await build_notebook_image(session)

    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'"))).fetchone()
    # Should be "null", not a real image URI
    assert row is not None
    assert row[0] == "null"


@pytest.mark.asyncio
async def test_poll_image_build_writes_uri_on_success(session, seed_build_config):
    """poll_image_build writes the image URI only when the build succeeds."""
    for key, value in [
        ("notebook_image_build_id", "build-success-1"),
        ("notebook_image_build_status", "WORKING"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    with patch(
        "app.services.notebook_image_service.check_build_status",
        return_value="SUCCESS",
    ):
        result = await poll_image_build(session)

    assert result == "SUCCESS"

    # Image URI should now be set
    row = (await session.execute(text("SELECT value FROM platform_config WHERE key = 'bioaf_scrna_image'"))).fetchone()
    assert row is not None
    assert row[0] == "us-central1-docker.pkg.dev/test-project/bioaf-images/bioaf-scrna:latest"
