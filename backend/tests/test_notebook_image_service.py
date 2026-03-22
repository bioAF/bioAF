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
