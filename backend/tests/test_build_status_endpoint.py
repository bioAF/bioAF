"""Tests for notebook image build status endpoint and component status passthrough."""

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def seed_platform(session, admin_user):
    """Seed minimal platform_config for build status tests."""
    for key, value in [
        ("compute_stack", "kubernetes"),
        ("compute_deployed", "true"),
        ("storage_deployed", "true"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    # Seed component_states
    for comp_key in ["nextflow", "rstudio", "jupyterhub", "cellxgene"]:
        await session.execute(
            text(
                "INSERT INTO component_states (component_key, enabled, status, config_json) "
                "VALUES (:key, false, 'disabled', '{}') "
                "ON CONFLICT (component_key) DO NOTHING"
            ),
            {"key": comp_key},
        )
    await session.commit()


# -----------------------------------------------------------------------
# GET /api/v1/infrastructure/notebook-image/build-status
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_status_no_build(client, session, admin_token, admin_user, seed_platform):
    """Returns null fields when no build has been triggered."""
    response = await client.get(
        "/api/v1/infrastructure/notebook-image/build-status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["build_id"] is None
    assert data["build_status"] is None
    assert data["image_uri"] is None


@pytest.mark.asyncio
async def test_build_status_working(client, session, admin_token, admin_user, seed_platform):
    """Returns build info when a build is in progress."""
    for key, value in [
        ("notebook_image_build_id", "build-abc-123"),
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

    response = await client.get(
        "/api/v1/infrastructure/notebook-image/build-status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["build_id"] == "build-abc-123"
    assert data["build_status"] == "WORKING"
    assert data["image_uri"] is None


@pytest.mark.asyncio
async def test_build_status_success_with_image(client, session, admin_token, admin_user, seed_platform):
    """Returns image URI when build succeeded."""
    for key, value in [
        ("notebook_image_build_id", "build-xyz-789"),
        ("notebook_image_build_status", "SUCCESS"),
        ("bioaf_scrna_image", "us-central1-docker.pkg.dev/proj/bioaf-images/bioaf-scrna:latest"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    response = await client.get(
        "/api/v1/infrastructure/notebook-image/build-status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["build_id"] == "build-xyz-789"
    assert data["build_status"] == "SUCCESS"
    assert data["image_uri"] == "us-central1-docker.pkg.dev/proj/bioaf-images/bioaf-scrna:latest"


@pytest.mark.asyncio
async def test_build_status_requires_auth(client, session, admin_user, seed_platform):
    """Endpoint requires authentication."""
    response = await client.get("/api/v1/infrastructure/notebook-image/build-status")
    assert response.status_code in (401, 403)


# -----------------------------------------------------------------------
# Component list should pass through "provisioning" status
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_components_list_shows_provisioning(client, session, admin_token, admin_user, seed_platform):
    """Components list returns 'provisioning' status for components that are building."""
    await session.execute(
        text("UPDATE component_states SET enabled = true, status = 'provisioning' WHERE component_key = 'rstudio'"),
    )
    await session.commit()

    response = await client.get(
        "/api/v1/infrastructure/stack/components",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    rstudio = next(c for c in data["components"] if c["key"] == "rstudio")
    assert rstudio["status"] == "provisioning"


@pytest.mark.asyncio
async def test_components_list_shows_build_failed(client, session, admin_token, admin_user, seed_platform):
    """Components list returns 'build_failed' status after a failed image build."""
    await session.execute(
        text("UPDATE component_states SET enabled = true, status = 'build_failed' WHERE component_key = 'rstudio'"),
    )
    await session.commit()

    response = await client.get(
        "/api/v1/infrastructure/stack/components",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    rstudio = next(c for c in data["components"] if c["key"] == "rstudio")
    assert rstudio["status"] == "build_failed"


# -----------------------------------------------------------------------
# POST /api/v1/infrastructure/notebook-image/cancel
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_build_no_active_build(client, session, admin_token, admin_user, seed_platform):
    """Cancel returns 400 when there is no active build."""
    response = await client.post(
        "/api/v1/infrastructure/notebook-image/cancel",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cancel_build_already_finished(client, session, admin_token, admin_user, seed_platform):
    """Cancel returns 400 when build already finished."""
    for key, value in [
        ("notebook_image_build_id", "build-done-1"),
        ("notebook_image_build_status", "FAILURE"),
    ]:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"k": key, "v": value},
        )
    await session.commit()

    response = await client.post(
        "/api/v1/infrastructure/notebook-image/cancel",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cancel_build_requires_admin(client, session, admin_user, seed_platform):
    """Cancel endpoint requires authentication."""
    response = await client.post("/api/v1/infrastructure/notebook-image/cancel")
    assert response.status_code in (401, 403)
