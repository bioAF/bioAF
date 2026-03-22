"""Tests for component toggle audit logging and improved notebook launch messages."""

import pytest
import pytest_asyncio
from sqlalchemy import text


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
    for comp_key in ["nextflow", "rstudio", "jupyterhub", "cellxgene"]:
        await session.execute(
            text(
                "INSERT INTO component_states (component_key, enabled, status, config_json) "
                "VALUES (:key, false, 'disabled', '{}') "
                "ON CONFLICT (component_key) DO NOTHING"
            ),
            {"key": comp_key},
        )
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('kubernetes_cluster', true, 'running', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'running'"
        ),
    )
    await session.commit()


# -----------------------------------------------------------------------
# Component toggle writes to audit_log
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_enable_creates_audit_entry(client, session, admin_token, admin_user, seed_deployed_stack):
    """Enabling a component writes an audit log entry."""
    response = await client.post(
        "/api/v1/infrastructure/stack/components/cellxgene/toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    row = (
        await session.execute(
            text(
                "SELECT action, entity_type, details_json FROM audit_log "
                "WHERE entity_type = 'component' ORDER BY id DESC LIMIT 1"
            )
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "enable"
    assert row[1] == "component"
    assert row[2]["component_key"] == "cellxgene"


@pytest.mark.asyncio
async def test_toggle_disable_creates_audit_entry(client, session, admin_token, admin_user, seed_deployed_stack):
    """Disabling a component writes an audit log entry."""
    # First enable
    await session.execute(
        text("UPDATE component_states SET enabled = true, status = 'enabled' WHERE component_key = 'cellxgene'"),
    )
    await session.commit()

    response = await client.post(
        "/api/v1/infrastructure/stack/components/cellxgene/toggle",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    row = (
        await session.execute(
            text(
                "SELECT action, entity_type, details_json FROM audit_log "
                "WHERE entity_type = 'component' ORDER BY id DESC LIMIT 1"
            )
        )
    ).fetchone()
    assert row is not None
    assert row[0] == "disable"
    assert row[1] == "component"
    assert row[2]["component_key"] == "cellxgene"


# -----------------------------------------------------------------------
# Notebook launch error messages should not say "environment"
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_while_building_says_image_not_environment(
    client, session, admin_token, admin_user, seed_deployed_stack
):
    """Error message when image is building uses clear, non-technical language."""
    for key, value in [
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

    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "rstudio"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "environment" not in detail.lower()
    assert "image" in detail.lower() or "building" in detail.lower()


@pytest.mark.asyncio
async def test_launch_no_image_says_enable_component(client, session, admin_token, admin_user, seed_deployed_stack):
    """Error when no image exists directs user to Components page."""
    response = await client.post(
        "/api/v1/notebooks/sessions",
        json={"session_type": "rstudio"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "environment" not in detail.lower()
    assert "Components" in detail
