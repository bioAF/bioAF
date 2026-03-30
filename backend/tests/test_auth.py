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
