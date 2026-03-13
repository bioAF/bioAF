"""Tests for GCP configuration API endpoints (GET, PUT, POST validate)."""

from unittest.mock import patch

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_gcp_defaults(session):
    """Insert the default GCP platform_config rows (mirrors migrations 022 + 029)."""
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('gcp_project_id',             ''),
            ('gcp_region',                 'us-central1'),
            ('gcp_zone',                   'us-central1-a'),
            ('org_slug',                   ''),
            ('gcp_credentials_configured', 'false'),
            ('gcp_validation_status',      ''),
            ('gcp_credential_source',      'vm_default'),
            ('gcp_service_account_email',  '')
        ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Test 7: GET returns defaults when no config exists
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_gcp_config_returns_defaults(client, admin_token, session):
    """GET /api/v1/settings/gcp returns defaults when platform_config is empty."""
    response = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "gcp_project_id" in data
    assert "gcp_region" in data
    assert "gcp_credentials_configured" in data


# ---------------------------------------------------------------------------
# Test 8: GET returns current config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_gcp_config_returns_stored_values(client, admin_token, session):
    """GET returns the stored config values."""
    await _seed_gcp_defaults(session)
    await session.execute(text("UPDATE platform_config SET value='my-proj' WHERE key='gcp_project_id'"))
    await session.commit()

    response = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gcp_project_id"] == "my-proj"


# ---------------------------------------------------------------------------
# Test 9: GET does not return service account key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_gcp_config_redacts_service_account_key(client, admin_token, session):
    """GET response must not expose service_account_key."""
    await _seed_gcp_defaults(session)
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) "
            "VALUES ('gcp_service_account_key', '{\"type\":\"service_account\"}') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )
    await session.commit()

    response = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "service_account_key" not in data
    assert "gcp_service_account_key" not in data


# ---------------------------------------------------------------------------
# Test 10: PUT updates config successfully
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_gcp_config_updates_fields(client, admin_token, session):
    """PUT /api/v1/settings/gcp saves the provided fields."""
    await _seed_gcp_defaults(session)

    response = await client.put(
        "/api/v1/settings/gcp",
        json={
            "gcp_project_id": "updated-proj",
            "gcp_region": "us-west1",
            "org_slug": "my-bioaf-org",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["gcp_project_id"] == "updated-proj"
    assert data["gcp_region"] == "us-west1"
    assert data["org_slug"] == "my-bioaf-org"


# ---------------------------------------------------------------------------
# Test 11: PUT returns 422 for invalid org_slug
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_gcp_config_invalid_org_slug(client, admin_token, session):
    """PUT returns 422 when org_slug fails validation."""
    await _seed_gcp_defaults(session)

    response = await client.put(
        "/api/v1/settings/gcp",
        json={"org_slug": "-invalid-"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 12: PUT resets gcp_validation_status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_gcp_config_resets_validation_status(client, admin_token, session):
    """PUT resets gcp_validation_status to empty and gcp_credentials_configured to false."""
    await _seed_gcp_defaults(session)
    # Simulate a previous successful validation
    await session.execute(text("UPDATE platform_config SET value='passed' WHERE key='gcp_validation_status'"))
    await session.execute(text("UPDATE platform_config SET value='true' WHERE key='gcp_credentials_configured'"))
    await session.commit()

    await client.put(
        "/api/v1/settings/gcp",
        json={"gcp_project_id": "new-proj"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    row = (await session.execute(text("SELECT value FROM platform_config WHERE key='gcp_validation_status'"))).scalar()
    assert row == ""


# ---------------------------------------------------------------------------
# Test 13: PUT writes audit log
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_gcp_config_writes_audit_log(client, admin_token, session):
    """PUT writes an entry to the audit_log table."""
    await _seed_gcp_defaults(session)

    await client.put(
        "/api/v1/settings/gcp",
        json={"gcp_project_id": "proj-audit-test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE entity_type='platform_config' AND action='update_gcp_config'")
        )
    ).scalar()
    assert count >= 1


# ---------------------------------------------------------------------------
# Test 15: GET returns 401 without auth
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_gcp_config_requires_auth(client):
    """GET /api/v1/settings/gcp returns 401 when no token provided."""
    response = await client.get("/api/v1/settings/gcp")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 17: PUT returns 403 for non-admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_gcp_config_requires_admin(client, viewer_token, session):
    """PUT /api/v1/settings/gcp returns 403 for non-admin users."""
    await _seed_gcp_defaults(session)

    response = await client.put(
        "/api/v1/settings/gcp",
        json={"gcp_project_id": "proj"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test 14: POST validate returns validation result
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_validate_returns_result(client, admin_token, session):
    """POST /api/v1/settings/gcp/validate returns validation checks."""
    await _seed_gcp_defaults(session)
    await session.execute(text("UPDATE platform_config SET value='my-proj' WHERE key='gcp_project_id'"))
    await session.commit()

    with patch("app.api.gcp_config.validate_gcp_credentials") as mock_validate:
        from app.schemas.gcp_config import GCPValidationCheck, GCPValidationResult

        mock_validate.return_value = GCPValidationResult(
            passed=False,
            checks=[GCPValidationCheck(name="credentials_loaded", passed=False, message="No creds")],
        )

        response = await client.post(
            "/api/v1/settings/gcp/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "passed" in data
    assert "checks" in data


# ---------------------------------------------------------------------------
# Test 16: POST validate writes audit log
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_validate_writes_audit_log(client, admin_token, session):
    """POST validate writes an audit log entry."""
    await _seed_gcp_defaults(session)
    await session.execute(text("UPDATE platform_config SET value='my-proj' WHERE key='gcp_project_id'"))
    await session.commit()

    with patch("app.api.gcp_config.validate_gcp_credentials") as mock_validate:
        from app.schemas.gcp_config import GCPValidationCheck, GCPValidationResult

        mock_validate.return_value = GCPValidationResult(
            passed=True,
            checks=[GCPValidationCheck(name="credentials_loaded", passed=True, message="OK")],
        )

        await client.post(
            "/api/v1/settings/gcp/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE entity_type='platform_config' AND action='validate_gcp_credentials'"
            )
        )
    ).scalar()
    assert count >= 1


# ---------------------------------------------------------------------------
# Test 18: PUT saves and GET returns service_account_email
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_service_account_email_round_trip(client, admin_token, session):
    """PUT saves gcp_service_account_email and GET returns it."""
    await _seed_gcp_defaults(session)

    response = await client.put(
        "/api/v1/settings/gcp",
        json={"gcp_service_account_email": "bioaf-sa@my-proj.iam.gserviceaccount.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["gcp_service_account_email"] == "bioaf-sa@my-proj.iam.gserviceaccount.com"

    get_resp = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["gcp_service_account_email"] == "bioaf-sa@my-proj.iam.gserviceaccount.com"


# ---------------------------------------------------------------------------
# Test 19: POST validate passes service_account_email to validator
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_validate_passes_sa_email(client, admin_token, session):
    """POST validate passes the stored service_account_email to the validation function."""
    await _seed_gcp_defaults(session)
    await session.execute(text("UPDATE platform_config SET value='my-proj' WHERE key='gcp_project_id'"))
    await session.execute(
        text("UPDATE platform_config SET value='sa@proj.iam.gserviceaccount.com' WHERE key='gcp_service_account_email'")
    )
    await session.commit()

    with patch("app.api.gcp_config.validate_gcp_credentials") as mock_validate:
        from app.schemas.gcp_config import GCPValidationCheck, GCPValidationResult

        mock_validate.return_value = GCPValidationResult(
            passed=True,
            checks=[GCPValidationCheck(name="credentials_loaded", passed=True, message="OK")],
        )

        await client.post(
            "/api/v1/settings/gcp/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        mock_validate.assert_called_once()
        call_kwargs = mock_validate.call_args
        assert call_kwargs.kwargs.get("service_account_email") == "sa@proj.iam.gserviceaccount.com"
