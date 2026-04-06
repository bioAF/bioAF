"""Tests for POST /api/v1/infrastructure/terraform/init endpoint."""

from unittest.mock import patch

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _seed_user_and_token(session, role_name="admin"):
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.bootstrap_roles import seed_builtin_roles

    org = Organization(name="InitTestOrg", setup_complete=True)
    session.add(org)
    await session.flush()

    role_map = await seed_builtin_roles(session, org.id)

    user = User(
        email=f"{role_name}_init@test.com",
        password_hash=AuthService.hash_password("pw"),
        role_id=role_map[role_name],
        organization_id=org.id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()

    token = AuthService.create_token(user.id, user.email, user.role_id, user.organization_id, role_name=role_name)
    return user, token


async def _seed_gcp_config(session, configured=True, initialized=False):
    rows = [
        ("gcp_credentials_configured", "true" if configured else "false"),
        ("gcp_credential_source", "vm_default"),
        ("gcp_project_id", "test-project"),
        ("gcp_region", "us-central1"),
        ("gcp_zone", "us-central1-a"),
        ("org_slug", "testorg"),
        ("terraform_initialized", "true" if initialized else "false"),
        ("terraform_state_bucket", "bioaf-tfstate-testorg" if initialized else ""),
    ]
    for key, value in rows:
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def test_terraform_init_success(client, session):
    """POST /terraform/init creates state bucket and runs terraform init."""
    user, token = await _seed_user_and_token(session)
    await _seed_gcp_config(session, configured=True, initialized=False)
    headers = {"Authorization": f"Bearer {token}"}

    with patch(
        "app.api.terraform_executor.TerraformExecutor.bootstrap_foundation",
        return_value=_fake_bootstrap_events(),
    ):
        resp = await client.post("/api/v1/infrastructure/terraform/init", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Terraform initialized"

    # The mock doesn't actually set terraform_initialized, but the endpoint
    # should have consumed the SSE generator without error.


async def test_terraform_init_returns_400_without_gcp(client, session):
    """POST /terraform/init returns 400 if GCP credentials are not configured."""
    user, token = await _seed_user_and_token(session)
    await _seed_gcp_config(session, configured=False, initialized=False)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/infrastructure/terraform/init", headers=headers)
    assert resp.status_code == 400


async def test_terraform_init_requires_permission(client, session):
    """POST /terraform/init requires infrastructure:deploy permission."""
    user, token = await _seed_user_and_token(session, role_name="viewer")
    await _seed_gcp_config(session, configured=True, initialized=False)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/infrastructure/terraform/init", headers=headers)
    assert resp.status_code == 403


async def _fake_bootstrap_events():
    """Yield fake progress events that simulate a successful bootstrap."""
    from app.services.terraform_executor import TerraformProgressEvent

    yield TerraformProgressEvent(event_type="progress", message="Starting foundation bootstrap...")
    yield TerraformProgressEvent(event_type="progress", message="Running terraform init...")
    yield TerraformProgressEvent(event_type="complete", message="Bootstrap complete")
