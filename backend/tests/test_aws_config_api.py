"""Tests for AWS configuration API endpoints (GET, PUT, POST validate).

DB-bound (CI-only locally). The validate path patches the STS seam in
``app.adapters.validation.aws`` so no real AWS call is made.
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

_STS_PATCH = "app.adapters.validation.aws._get_sts_client"


def _fake_sts(account="043671579834", arn="arn:aws:iam::043671579834:user/brent"):
    client = MagicMock()
    client.get_caller_identity.return_value = {"Account": account, "Arn": arn}
    return client


async def _seed_aws_defaults(session):
    """Insert default AWS platform_config rows (mirrors install-aws.sh prefill)."""
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('aws_account_id',             '043671579834'),
            ('aws_region',                 'us-west-1'),
            ('aws_app_role_arn',           'arn:aws:iam::043671579834:role/bioaf-app'),
            ('aws_credential_source',      'instance_profile'),
            ('aws_credentials_configured', 'false'),
            ('aws_validation_status',      ''),
            ('org_slug',                   '')
        ON CONFLICT (key) DO NOTHING
        """)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_get_aws_config_returns_defaults(client, admin_token, session):
    """GET returns defaults (no rows seeded) without error."""
    response = await client.get(
        "/api/v1/settings/aws",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "aws_account_id" in data
    assert "aws_region" in data
    assert data["aws_credential_source"] == "instance_profile"
    assert data["aws_credentials_configured"] is False


@pytest.mark.asyncio
async def test_get_aws_config_returns_stored_values(client, admin_token, session):
    await _seed_aws_defaults(session)
    response = await client.get(
        "/api/v1/settings/aws",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["aws_account_id"] == "043671579834"
    assert data["aws_region"] == "us-west-1"
    assert data["aws_app_role_arn"] == "arn:aws:iam::043671579834:role/bioaf-app"


@pytest.mark.asyncio
async def test_put_aws_config_updates_fields(client, admin_token, session):
    await _seed_aws_defaults(session)
    response = await client.put(
        "/api/v1/settings/aws",
        json={"aws_region": "us-east-2", "org_slug": "my-bioaf-org"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["aws_region"] == "us-east-2"
    assert data["org_slug"] == "my-bioaf-org"


@pytest.mark.asyncio
async def test_put_aws_config_invalid_org_slug(client, admin_token, session):
    await _seed_aws_defaults(session)
    response = await client.put(
        "/api/v1/settings/aws",
        json={"org_slug": "-invalid-"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_aws_config_resets_validation_state(client, admin_token, session):
    await _seed_aws_defaults(session)
    await session.execute(text("UPDATE platform_config SET value='passed' WHERE key='aws_validation_status'"))
    await session.execute(text("UPDATE platform_config SET value='true' WHERE key='aws_credentials_configured'"))
    await session.commit()

    await client.put(
        "/api/v1/settings/aws",
        json={"aws_region": "eu-west-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    status = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='aws_validation_status'"))
    ).scalar()
    configured = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='aws_credentials_configured'"))
    ).scalar()
    assert status == ""
    assert configured == "false"


@pytest.mark.asyncio
async def test_put_aws_config_writes_audit_log(client, admin_token, session):
    await _seed_aws_defaults(session)
    await client.put(
        "/api/v1/settings/aws",
        json={"aws_region": "us-east-1"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE entity_type='platform_config' AND action='update_aws_config'")
        )
    ).scalar()
    assert count >= 1


@pytest.mark.asyncio
async def test_validate_aws_config_passes_and_persists(client, admin_token, session):
    await _seed_aws_defaults(session)
    with patch(_STS_PATCH, return_value=_fake_sts()):
        response = await client.post(
            "/api/v1/settings/aws/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is True
    assert data["account_id"] == "043671579834"

    configured = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='aws_credentials_configured'"))
    ).scalar()
    assert configured == "true"


@pytest.mark.asyncio
async def test_validate_aws_config_account_mismatch_fails(client, admin_token, session):
    await _seed_aws_defaults(session)
    with patch(_STS_PATCH, return_value=_fake_sts(account="999999999999")):
        response = await client.post(
            "/api/v1/settings/aws/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    assert response.json()["passed"] is False
    configured = (
        await session.execute(text("SELECT value FROM platform_config WHERE key='aws_credentials_configured'"))
    ).scalar()
    assert configured == "false"


@pytest.mark.asyncio
async def test_get_aws_config_requires_auth(client):
    response = await client.get("/api/v1/settings/aws")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_put_aws_config_requires_admin(client, viewer_token, session):
    await _seed_aws_defaults(session)
    response = await client.put(
        "/api/v1/settings/aws",
        json={"aws_region": "us-east-1"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403
