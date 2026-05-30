import pytest
from httpx import AsyncClient
from sqlalchemy import text


pytestmark = pytest.mark.asyncio


async def _get_setup_token(client: AsyncClient) -> str:
    """Generate a setup code and verify it, returning the setup token."""
    gen_resp = await client.post("/api/bootstrap/generate-setup-code")
    code = gen_resp.json()["code"]
    verify_resp = await client.post(
        "/api/bootstrap/verify-setup-code",
        json={"code": code},
    )
    return verify_resp.json()["setup_token"]


async def test_bootstrap_status_no_org(client: AsyncClient):
    response = await client.get("/api/bootstrap/status")
    assert response.status_code == 200
    assert response.json()["setup_complete"] is False


async def test_create_admin(client: AsyncClient):
    setup_token = await _get_setup_token(client)
    response = await client.post(
        "/api/bootstrap/create-admin",
        json={
            "email": "newadmin@test.com",
            "password": "securepassword123",
            "name": "Test Admin",
        },
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["message"] == "Admin account created"


async def test_create_admin_twice_fails(client: AsyncClient):
    setup_token = await _get_setup_token(client)
    # First call
    await client.post(
        "/api/bootstrap/create-admin",
        json={
            "email": "admin1@test.com",
            "password": "password123",
        },
        headers={"Authorization": f"Bearer {setup_token}"},
    )

    # Second call should fail (need a new setup token since code was consumed)
    # Generate a new code first
    gen_resp = await client.post("/api/bootstrap/generate-setup-code")
    data = gen_resp.json()
    # Admin already exists, so should be already_setup
    assert data["already_setup"] is True


async def test_full_bootstrap_flow(client: AsyncClient):
    # Step 1: Generate and verify setup code
    setup_token = await _get_setup_token(client)

    # Step 2: Create admin
    resp = await client.post(
        "/api/bootstrap/create-admin",
        json={
            "email": "admin@bootstrap.com",
            "password": "password123",
        },
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Step 3: Configure org
    resp = await client.post(
        "/api/bootstrap/configure-org",
        json={
            "org_name": "Test Biotech",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    # Step 4: Complete setup
    resp = await client.post("/api/bootstrap/complete", headers=headers)
    assert resp.status_code == 200

    # Verify setup is complete
    resp = await client.get("/api/bootstrap/status")
    assert resp.json()["setup_complete"] is True


async def test_bootstrap_status_has_in_flight_when_components_queued(client: AsyncClient, session):
    """T11a: a queued_for_infra row means the wizard should route returning
    users back to the components view, not the dashboard.
    """
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('nextflow', true, 'queued_for_infra', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'queued_for_infra'"
        )
    )
    await session.commit()

    response = await client.get("/api/bootstrap/status")
    assert response.status_code == 200
    assert response.json()["has_in_flight_components"] is True


async def test_bootstrap_status_has_in_flight_when_components_provisioning(client: AsyncClient, session):
    """T11b: provisioning (mid-image-build) also counts as in-flight."""
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('jupyterhub', true, 'provisioning', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET enabled = true, status = 'provisioning'"
        )
    )
    await session.commit()

    response = await client.get("/api/bootstrap/status")
    assert response.status_code == 200
    assert response.json()["has_in_flight_components"] is True


async def test_bootstrap_status_no_in_flight_when_only_enabled_or_disabled(client: AsyncClient, session):
    """T11c: rows in steady states do not count as in-flight."""
    await session.execute(
        text(
            "INSERT INTO component_states (component_key, enabled, status, config_json) "
            "VALUES ('nextflow', true, 'enabled', '{}'), ('rstudio', false, 'disabled', '{}') "
            "ON CONFLICT (component_key) DO UPDATE SET status = EXCLUDED.status, enabled = EXCLUDED.enabled"
        )
    )
    await session.commit()

    response = await client.get("/api/bootstrap/status")
    assert response.status_code == 200
    assert response.json()["has_in_flight_components"] is False


async def test_bootstrap_status_no_in_flight_when_table_empty(client: AsyncClient):
    """T11d: a fresh install has no rows; the field is False, not omitted."""
    response = await client.get("/api/bootstrap/status")
    assert response.status_code == 200
    assert response.json()["has_in_flight_components"] is False


async def test_bootstrap_requires_admin_role(client: AsyncClient, viewer_token: str):
    response = await client.post(
        "/api/bootstrap/configure-org",
        json={
            "org_name": "Test",
        },
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
