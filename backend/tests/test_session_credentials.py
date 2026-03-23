import pytest
from httpx import AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_auto_generate_username_from_email():
    """Username generation strips domain, removes dots and special chars."""
    from app.services.session_credential_service import SessionCredentialService

    assert SessionCredentialService.generate_username("brent.c.mills@gmail.com") == "brentcmills"
    assert SessionCredentialService.generate_username("sarah@bioaf-demo.org") == "sarah"
    assert SessionCredentialService.generate_username("alex.jones+work@company.io") == "alexjoneswork"
    assert SessionCredentialService.generate_username("UPPER.Case@test.com") == "uppercase"


@pytest.mark.asyncio
async def test_create_session_credentials(client: AsyncClient, admin_token: str, admin_user, session):
    """User can create session credentials via profile endpoint."""
    resp = await client.put(
        "/api/auth/me/session-credentials",
        json={"password": "mysecurepassword"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"  # from admin@test.com
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_get_session_credentials(client: AsyncClient, admin_token: str, admin_user):
    """User can retrieve their session credentials."""
    # Create first
    await client.put(
        "/api/auth/me/session-credentials",
        json={"password": "mysecurepassword"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Get
    resp = await client.get(
        "/api/auth/me/session-credentials",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["configured"] is True


@pytest.mark.asyncio
async def test_get_session_credentials_not_configured(client: AsyncClient, admin_token: str, admin_user):
    """Returns configured=False when no credentials exist."""
    resp = await client.get(
        "/api/auth/me/session-credentials",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False


@pytest.mark.asyncio
async def test_custom_username(client: AsyncClient, admin_token: str, admin_user):
    """User can set a custom username."""
    resp = await client.put(
        "/api/auth/me/session-credentials",
        json={"username": "bmills", "password": "mysecurepassword"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "bmills"


@pytest.mark.asyncio
async def test_update_session_password(client: AsyncClient, admin_token: str, admin_user):
    """User can update their session password."""
    # Create
    await client.put(
        "/api/auth/me/session-credentials",
        json={"password": "firstpassword"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Update password only
    resp = await client.put(
        "/api/auth/me/session-credentials",
        json={"password": "newpassword"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_username_collision_appends_suffix(client: AsyncClient, admin_token: str, admin_user, session):
    """When auto-generated username collides, a numeric suffix is appended."""
    from app.services.session_credential_service import SessionCredentialService

    # Create credentials for admin user (username: "admin")
    await SessionCredentialService.create_or_update(
        session,
        user_id=admin_user.id,
        org_id=admin_user.organization_id,
        email=admin_user.email,
        password="pass1",
    )
    await session.commit()

    # Create a second user with same username prefix
    from app.models.user import User
    from app.services.auth_service import AuthService

    user2 = User(
        email="admin@other.com",
        password_hash=AuthService.hash_password("pass"),
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user2)
    await session.flush()

    cred = await SessionCredentialService.create_or_update(
        session,
        user_id=user2.id,
        org_id=user2.organization_id,
        email=user2.email,
        password="pass2",
    )
    await session.commit()

    # Should have gotten a suffixed username
    assert cred.username == "admin2"


@pytest.mark.asyncio
async def test_username_validation_rejects_invalid(client: AsyncClient, admin_token: str, admin_user):
    """Custom usernames must be alphanumeric + underscores, 3-32 chars."""
    resp = await client.put(
        "/api/auth/me/session-credentials",
        json={"username": "ab", "password": "pass"},  # too short
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422

    resp = await client.put(
        "/api/auth/me/session-credentials",
        json={"username": "user with spaces", "password": "pass"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_user_list_includes_session_credentials_configured(
    client: AsyncClient,
    admin_token: str,
    admin_user,
):
    """Admin user list response includes session_credentials_configured field."""
    resp = await client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    user = next(u for u in data["users"] if u["email"] == "admin@test.com")
    assert "session_credentials_configured" in user
    assert user["session_credentials_configured"] is False


@pytest.mark.asyncio
async def test_user_list_includes_last_login(
    client: AsyncClient,
    admin_token: str,
    admin_user,
):
    """Admin user list response includes last_login field."""
    resp = await client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    user = next(u for u in data["users"] if u["email"] == "admin@test.com")
    assert "last_login" in user


@pytest.mark.asyncio
async def test_login_updates_last_login(client: AsyncClient, admin_user, session):
    """Logging in sets the last_login timestamp."""
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "testpassword123"},
    )
    assert resp.status_code == 200

    result = await session.execute(
        text("SELECT last_login FROM users WHERE id = :id"),
        {"id": admin_user.id},
    )
    row = result.fetchone()
    assert row[0] is not None
