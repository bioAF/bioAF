"""Tests for component toggle triggering notebook image build."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from unittest.mock import patch, AsyncMock


@pytest_asyncio.fixture
async def seed_deployed_stack(session, admin_user):
    """Seed platform_config for a deployed compute stack with components."""
    for key, value in [
        ("compute_stack", "kubernetes"),
        ("compute_deployed", "true"),
        ("storage_deployed", "true"),
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
    # Create component_states rows
    for comp_key in ["nextflow", "rstudio", "jupyterhub", "cellxgene"]:
        await session.execute(
            text(
                "INSERT INTO component_states (component_key, enabled, status, config_json) "
                "VALUES (:key, false, 'disabled', '{}') "
                "ON CONFLICT (component_key) DO NOTHING"
            ),
            {"key": comp_key},
        )
    # kubernetes_cluster must be enabled for dependency checks
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('kubernetes_cluster', true, 'running', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'running'"
        ),
    )
    await session.commit()


@pytest.mark.asyncio
async def test_toggle_rstudio_triggers_image_build(client, session, admin_token, admin_user, seed_deployed_stack):
    """Enabling RStudio triggers notebook image build when image is not set."""
    with patch(
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
        return_value="build-789",
    ) as mock_build:
        response = await client.post(
            "/api/v1/infrastructure/stack/components/rstudio/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "provisioning"
    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_rstudio_skips_build_when_image_exists_and_build_succeeded(
    client, session, admin_token, admin_user, seed_deployed_stack
):
    """Enabling RStudio skips build only if image URI is set AND build status is SUCCESS."""
    for key, value in [
        ("bioaf_scrna_image", "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"),
        ("notebook_image_build_status", "SUCCESS"),
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
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
    ) as mock_build:
        response = await client.post(
            "/api/v1/infrastructure/stack/components/rstudio/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "enabled"
    mock_build.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_rstudio_rebuilds_when_image_exists_but_build_failed(
    client, session, admin_token, admin_user, seed_deployed_stack
):
    """Stale image URI from a failed build triggers a fresh build on re-enable."""
    for key, value in [
        ("bioaf_scrna_image", "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"),
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

    with patch(
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
        return_value="rebuild-123",
    ) as mock_build:
        response = await client.post(
            "/api/v1/infrastructure/stack/components/rstudio/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "provisioning"
    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_rstudio_rebuilds_when_no_build_status(
    client, session, admin_token, admin_user, seed_deployed_stack
):
    """Image URI without any build status (pre-fix data) triggers a build."""
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": "bioaf_scrna_image", "v": "us-central1-docker.pkg.dev/test/repo/bioaf-scrna:latest"},
    )
    await session.commit()

    with patch(
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
        return_value="rebuild-456",
    ) as mock_build:
        response = await client.post(
            "/api/v1/infrastructure/stack/components/rstudio/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "provisioning"
    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_cellxgene_does_not_trigger_build(client, session, admin_token, admin_user, seed_deployed_stack):
    """Enabling non-notebook components does not trigger image build."""
    with patch(
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
    ) as mock_build:
        response = await client.post(
            "/api/v1/infrastructure/stack/components/cellxgene/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "enabled"
    mock_build.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_rstudio_still_enables_on_build_failure(
    client, session, admin_token, admin_user, seed_deployed_stack
):
    """If image build fails, component shows build_failed status."""
    with patch(
        "app.api.stack_deploy.build_notebook_image",
        new_callable=AsyncMock,
        side_effect=ValueError("GCP project not configured"),
    ):
        response = await client.post(
            "/api/v1/infrastructure/stack/components/rstudio/toggle",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["status"] == "build_failed"
