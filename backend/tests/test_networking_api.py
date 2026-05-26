"""Tests for the networking settings API (hostname/domain, reachability, TLS)."""

import pytest


@pytest.mark.asyncio
async def test_get_networking_returns_defaults(client, admin_token, session):
    """GET /api/v1/settings/networking returns empty defaults for a fresh install."""
    response = await client.get(
        "/api/v1/settings/networking",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["hostname"] == ""
    assert data["domain"] == ""
    assert data["fqdn"] == ""
    assert data["reachability_status"] == ""
    assert data["reachability_checked_at"] is None
    assert data["cert_status"] == ""
    assert data["https_enforced"] is False
