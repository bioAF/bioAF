import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "testpassword123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_password(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "wrongpassword",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "nobody@test.com",
            "password": "password",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/auth/me",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "admin@test.com"
    assert data["role_name"] == "admin"


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/auth/refresh",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


@pytest.mark.asyncio
async def test_login_deactivated_user(client: AsyncClient, session, admin_user):
    admin_user.status = "deactivated"
    await session.flush()
    await session.commit()

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "testpassword123",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rate_limiting(client: AsyncClient, admin_user):
    """Test that rate limiting kicks in after too many requests."""
    for _ in range(10):
        await client.post(
            "/api/auth/login",
            json={
                "email": "admin@test.com",
                "password": "wrongpassword",
            },
        )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "wrongpassword",
        },
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_request_password_reset(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/auth/request-reset",
        json={
            "email": "admin@test.com",
        },
    )
    assert response.status_code == 200


def _capture_reset_email(monkeypatch):
    """Patch the reset email so tests can read the link and code the user would receive."""
    from app.services.email_service import EmailService

    sent: dict = {}

    def fake_send(to, code, reset_link):
        sent.update(to=to, code=code, link=reset_link)
        return True

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(fake_send))
    return sent


async def _start_reset(client, monkeypatch, email="admin@test.com"):
    sent = _capture_reset_email(monkeypatch)
    resp = await client.post("/api/auth/request-reset", json={"email": email})
    assert resp.status_code == 200
    assert "token=" in sent["link"], "reset email must contain a tokenized link"
    token = sent["link"].split("token=", 1)[1]
    return token, sent["code"]


@pytest.mark.asyncio
async def test_request_reset_sends_link_and_60min_code(client: AsyncClient, admin_user, session, monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.component import VerificationCode

    token, code = await _start_reset(client, monkeypatch)
    assert token
    assert len(code) == 6 and code.isdigit()

    result = await session.execute(
        select(VerificationCode).where(VerificationCode.token == token)
    )
    row = result.scalar_one()
    assert row.purpose == "password_reset"
    assert row.used is False
    remaining = (row.expires_at - datetime.now(timezone.utc)).total_seconds()
    assert 55 * 60 < remaining <= 60 * 60


@pytest.mark.asyncio
async def test_validate_reset_token_valid(client: AsyncClient, admin_user, monkeypatch):
    token, _ = await _start_reset(client, monkeypatch)
    resp = await client.get(f"/api/auth/reset-password/validate?token={token}")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.asyncio
async def test_validate_reset_token_invalid_for_unknown(client: AsyncClient, admin_user):
    resp = await client.get("/api/auth/reset-password/validate?token=does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_reset_token_invalid_after_use(client: AsyncClient, admin_user, monkeypatch):
    token, code = await _start_reset(client, monkeypatch)
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "code": code, "new_password": "freshpass123"},
    )
    assert resp.status_code == 200
    resp = await client.get(f"/api/auth/reset-password/validate?token={token}")
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_reset_password_with_token_and_code(client: AsyncClient, admin_user, monkeypatch):
    token, code = await _start_reset(client, monkeypatch)
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "code": code, "new_password": "freshpass123"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "freshpass123"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_reset_password_bad_token_returns_400(client: AsyncClient, admin_user, monkeypatch):
    _, code = await _start_reset(client, monkeypatch)
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": "bogus-token", "code": code, "new_password": "freshpass123"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reset_password_wrong_code_returns_400(client: AsyncClient, admin_user, monkeypatch):
    token, code = await _start_reset(client, monkeypatch)
    wrong = "000000" if code != "000000" else "111111"
    resp = await client.post(
        "/api/auth/reset-password",
        json={"token": token, "code": wrong, "new_password": "freshpass123"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_own_name_succeeds(client: AsyncClient, admin_token: str):
    resp = await client.patch(
        "/api/auth/me",
        json={"name": "Ada Lovelace"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Ada Lovelace"


@pytest.mark.asyncio
async def test_update_own_name_rejects_empty(client: AsyncClient, admin_token: str):
    resp = await client.patch(
        "/api/auth/me",
        json={"name": "   "},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unauthorized_without_token(client: AsyncClient):
    response = await client.get("/api/users")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_query_param_token_rejected_on_non_content_path(client: AsyncClient, admin_token: str):
    """JWT in query parameter must be rejected on non-file-content endpoints."""
    response = await client.get(
        f"/api/auth/me?token={admin_token}",
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_query_param_token_rejected_on_api_endpoints(client: AsyncClient, admin_token: str):
    """Endpoints like /api/users must not accept tokens via query params."""
    response = await client.get(
        f"/api/users?token={admin_token}",
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_header_still_works(client: AsyncClient, admin_token: str):
    """Authorization header must continue to work on all endpoints."""
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
