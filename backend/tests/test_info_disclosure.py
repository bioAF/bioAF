"""Tests for unauthenticated information disclosure (pentest finding #3).

Bootstrap status must not leak smtp_configured to unauthenticated callers.
Health service detail must require authentication.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_bootstrap_status_omits_smtp_when_unauthenticated(client: AsyncClient):
    """Unauthenticated /bootstrap/status must not include smtp_configured."""
    resp = await client.get("/api/bootstrap/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "smtp_configured" not in data
    # Fields the setup wizard needs should still be present
    assert "setup_complete" in data
    assert "has_setup_code" in data
    assert "has_admin" in data


@pytest.mark.asyncio
async def test_bootstrap_status_includes_smtp_when_authenticated(client: AsyncClient, admin_token: str):
    """Authenticated /bootstrap/status includes smtp_configured."""
    resp = await client.get(
        "/api/bootstrap/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "smtp_configured" in data


@pytest.mark.asyncio
async def test_health_services_requires_auth(client: AsyncClient):
    """/api/health/services must not be accessible without auth."""
    resp = await client.get("/api/health/services")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_services_accessible_with_auth(client: AsyncClient, admin_token: str):
    """/api/health/services is accessible with valid token."""
    resp = await client.get(
        "/api/health/services",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_status_requires_auth(client: AsyncClient):
    """/api/health/status must not be accessible without auth."""
    resp = await client.get("/api/health/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_liveness_stays_public(client: AsyncClient):
    """Liveness probe must remain public for container orchestration."""
    resp = await client.get("/api/health/live")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_readiness_stays_public(client: AsyncClient):
    """Readiness probe must remain public for container orchestration."""
    resp = await client.get("/api/health/ready")
    assert resp.status_code == 200
