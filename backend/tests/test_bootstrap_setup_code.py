"""Tests for setup code bootstrap endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _generate_code(client: AsyncClient) -> dict:
    """Helper: call generate-setup-code and return JSON response."""
    resp = await client.post("/api/bootstrap/generate-setup-code")
    assert resp.status_code == 200
    return resp.json()


async def _verify_code(client: AsyncClient, code: str) -> dict:
    """Helper: call verify-setup-code and return the response object."""
    return await client.post(
        "/api/bootstrap/verify-setup-code",
        json={"code": code},
    )


async def test_generate_setup_code_creates_org(client: AsyncClient, session):
    """POST /generate-setup-code creates org if none exists, returns code."""
    data = await _generate_code(client)

    assert data["already_setup"] is False
    assert data["code"] is not None
    assert len(data["code"]) == 6
    assert data["expires_at"] is not None

    # Org should exist now
    result = await session.execute(text("SELECT count(*) FROM organizations"))
    assert result.scalar() == 1


async def test_generate_setup_code_returns_new_code_when_expired(client: AsyncClient, session):
    """POST /generate-setup-code returns a fresh code when previous expired."""
    await _generate_code(client)

    # Expire the code manually
    await session.execute(text("UPDATE organizations SET setup_code_expires_at = NOW() - interval '1 hour'"))
    await session.commit()

    data2 = await _generate_code(client)
    code2 = data2["code"]

    assert data2["already_setup"] is False
    assert code2 is not None
    # New code may or may not differ, but should be valid
    assert len(code2) == 6


async def test_generate_setup_code_returns_already_setup_when_admin_exists(client: AsyncClient):
    """POST /generate-setup-code returns already_setup: true when admin exists."""
    # Create org + admin via generate + verify + create-admin flow
    gen_data = await _generate_code(client)
    verify_resp = await client.post(
        "/api/bootstrap/verify-setup-code",
        json={"code": gen_data["code"]},
    )
    setup_token = verify_resp.json()["setup_token"]

    await client.post(
        "/api/bootstrap/create-admin",
        json={"email": "admin@test.com", "password": "password123", "name": "Admin"},
        headers={"Authorization": f"Bearer {setup_token}"},
    )

    # Now generate-setup-code should say already_setup
    data = await _generate_code(client)
    assert data["already_setup"] is True
    assert data["code"] is None


async def test_verify_setup_code_returns_jwt(client: AsyncClient):
    """POST /verify-setup-code returns setup JWT on valid code."""
    gen_data = await _generate_code(client)
    resp = await _verify_code(client, gen_data["code"])

    assert resp.status_code == 200
    data = resp.json()
    assert "setup_token" in data
    assert data["message"] == "Setup code verified"


async def test_verify_setup_code_returns_401_on_invalid(client: AsyncClient):
    """POST /verify-setup-code returns 401 on invalid code."""
    await _generate_code(client)
    resp = await _verify_code(client, "ZZZZZZ")

    assert resp.status_code == 401


async def test_verify_setup_code_returns_401_on_expired(client: AsyncClient, session):
    """POST /verify-setup-code returns 401 when code has expired."""
    gen_data = await _generate_code(client)

    # Expire it
    await session.execute(text("UPDATE organizations SET setup_code_expires_at = NOW() - interval '1 hour'"))
    await session.commit()

    resp = await _verify_code(client, gen_data["code"])
    assert resp.status_code == 401


async def test_bootstrap_status_includes_new_fields(client: AsyncClient):
    """GET /status includes has_setup_code and has_admin fields."""
    resp = await client.get("/api/bootstrap/status")
    data = resp.json()

    assert "has_setup_code" in data
    assert "has_admin" in data
    assert data["has_setup_code"] is False
    assert data["has_admin"] is False


async def test_bootstrap_status_reflects_setup_code(client: AsyncClient):
    """GET /status shows has_setup_code=True after code generation."""
    await _generate_code(client)

    resp = await client.get("/api/bootstrap/status")
    data = resp.json()
    assert data["has_setup_code"] is True
    assert data["has_admin"] is False


async def test_bootstrap_status_reflects_admin(client: AsyncClient):
    """GET /status shows has_admin=True after admin creation."""
    gen_data = await _generate_code(client)
    verify_resp = await client.post(
        "/api/bootstrap/verify-setup-code",
        json={"code": gen_data["code"]},
    )
    setup_token = verify_resp.json()["setup_token"]

    await client.post(
        "/api/bootstrap/create-admin",
        json={"email": "admin@test.com", "password": "password123", "name": "Admin"},
        headers={"Authorization": f"Bearer {setup_token}"},
    )

    resp = await client.get("/api/bootstrap/status")
    data = resp.json()
    assert data["has_admin"] is True
