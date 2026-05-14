import pytest
from httpx import AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_login_creates_access_log_entry(client: AsyncClient, admin_user, session):
    """Logging in should create an access_log entry with resource_type='auth'."""
    response = await client.post(
        "/api/auth/login",
        json={"email": admin_user.email, "password": "testpassword123"},
    )
    assert response.status_code == 200

    result = await session.execute(text("SELECT * FROM access_log WHERE resource_type = 'auth' AND action = 'login'"))
    rows = result.fetchall()
    assert len(rows) >= 1
    assert rows[0].user_id == admin_user.id


@pytest.mark.asyncio
async def test_never_logged_in_users(client: AsyncClient, admin_token: str, admin_user, session):
    """Should list users who have never logged in."""
    response = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["users"], list)


@pytest.mark.asyncio
async def test_list_access_logs_empty(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/access-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["logs"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_access_logs_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/access-logs",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_access_logs_with_data(client: AsyncClient, admin_token: str, admin_user, session):
    from app.services.access_log_service import AccessLogService

    await AccessLogService.log_access(
        session,
        admin_user.organization_id,
        admin_user.id,
        "file",
        "123",
        "download",
        {"filename": "data.csv"},
    )
    await AccessLogService.log_access(
        session,
        admin_user.organization_id,
        admin_user.id,
        "notebook",
        "456",
        "session",
        {"notebook_name": "analysis.ipynb"},
    )
    await session.commit()

    response = await client.get(
        "/api/access-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2


async def _invite_and_backdate(client, admin_token, session, email, role_id, days_old=3):
    resp = await client.post(
        "/api/users",
        json={"email": email, "role_id": role_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]
    await session.execute(
        text("UPDATE users SET created_at = NOW() - (:days * INTERVAL '1 day') WHERE id = :id"),
        {"days": days_old, "id": user_id},
    )
    await session.commit()
    return user_id


@pytest.mark.asyncio
async def test_never_logged_in_excludes_deactivated(client: AsyncClient, admin_token: str, admin_user, session):
    """Deactivated users should not appear in the never-logged-in list."""
    role_map = admin_user._test_role_map
    user_id = await _invite_and_backdate(client, admin_token, session, "ghost@test.com", role_map["bench"])

    # Should appear in never-logged-in
    resp = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert any(u["id"] == user_id for u in resp.json()["users"])

    # Deactivate the user
    resp = await client.post(
        f"/api/users/{user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Should no longer appear in never-logged-in
    resp = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert not any(u["id"] == user_id for u in resp.json()["users"])


@pytest.mark.asyncio
async def test_never_logged_in_excludes_recent_invites(client: AsyncClient, admin_token: str, admin_user, session):
    """Users invited within the grace window should not appear."""
    role_map = admin_user._test_role_map
    # Just-invited user: created_at = NOW(), well inside the 2-day grace
    resp = await client.post(
        "/api/users",
        json={"email": "fresh@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    fresh_id = resp.json()["id"]

    # Older invite (3 days old) for comparison
    aged_id = await _invite_and_backdate(client, admin_token, session, "aged@test.com", role_map["bench"])

    resp = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    ids = {u["id"] for u in resp.json()["users"]}
    assert fresh_id not in ids
    assert aged_id in ids


@pytest.mark.asyncio
async def test_never_logged_in_excludes_service_accounts(client: AsyncClient, admin_token: str, admin_user, session):
    """Service-account users should not appear (they authenticate via API keys)."""
    role_map = admin_user._test_role_map
    # Create a service-account user directly: the public users API does not
    # expose is_service_account=True, so insert via SQL with a 3-day backdate
    # so the grace window cannot mask the assertion.
    await session.execute(
        text(
            "INSERT INTO users (organization_id, email, password_hash, role_id, status, "
            "is_service_account, created_at, updated_at) "
            "VALUES (:org, :email, 'x', :role, 'active', true, NOW() - INTERVAL '3 days', NOW())"
        ),
        {"org": admin_user.organization_id, "email": "sa-bot@test.bioaf.svc", "role": role_map["bench"]},
    )
    await session.commit()

    resp = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    emails = {u["email"] for u in resp.json()["users"]}
    assert "sa-bot@test.bioaf.svc" not in emails


@pytest.mark.asyncio
async def test_never_logged_in_includes_role_name(client: AsyncClient, admin_token: str, admin_user, session):
    """Response should include role_name so the UI can render it directly."""
    role_map = admin_user._test_role_map
    user_id = await _invite_and_backdate(client, admin_token, session, "rolecheck@test.com", role_map["comp_bio"])

    resp = await client.get(
        "/api/access-logs/never-logged-in",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    row = next(u for u in resp.json()["users"] if u["id"] == user_id)
    assert row["role_name"] == "comp_bio"


@pytest.mark.asyncio
async def test_filter_access_logs_by_resource_type(client: AsyncClient, admin_token: str, admin_user, session):
    from app.services.access_log_service import AccessLogService

    await AccessLogService.log_access(
        session,
        admin_user.organization_id,
        admin_user.id,
        "file",
        "1",
        "download",
    )
    await AccessLogService.log_access(
        session,
        admin_user.organization_id,
        admin_user.id,
        "notebook",
        "2",
        "session",
    )
    await session.commit()

    response = await client.get(
        "/api/access-logs?resource_type=file",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    for log in response.json()["logs"]:
        assert log["resource_type"] == "file"
