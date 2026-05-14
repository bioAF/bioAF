import pytest
from httpx import AsyncClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, admin_token: str, admin_user):
    response = await client.get(
        "/api/users",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any(u["email"] == "admin@test.com" for u in data["users"])


@pytest.mark.asyncio
async def test_list_users_excludes_service_accounts(client: AsyncClient, admin_token: str, admin_user, session):
    """Service accounts must not appear in the Users tab; they have their own tab."""
    role_map = admin_user._test_role_map
    await session.execute(
        text(
            "INSERT INTO users (organization_id, email, password_hash, role_id, status, "
            "is_service_account, created_at, updated_at) "
            "VALUES (:org, :email, 'x', :role, 'active', true, NOW(), NOW())"
        ),
        {"org": admin_user.organization_id, "email": "sa-bot@test.bioaf.svc", "role": role_map["bench"]},
    )
    await session.commit()

    response = await client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()["users"]}
    assert "sa-bot@test.bioaf.svc" not in emails
    assert "admin@test.com" in emails


@pytest.mark.asyncio
async def test_invite_user(client: AsyncClient, admin_token: str, admin_user):
    role_map = admin_user._test_role_map
    response = await client.post(
        "/api/users",
        json={
            "email": "newuser@test.com",
            "role_id": role_map["comp_bio"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "newuser@test.com"
    assert data["role_name"] == "comp_bio"
    assert data["status"] == "invited"


@pytest.mark.asyncio
async def test_invite_duplicate_email(client: AsyncClient, admin_token: str, admin_user):
    role_map = admin_user._test_role_map
    response = await client.post(
        "/api/users",
        json={
            "email": "admin@test.com",
            "role_id": role_map["viewer"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_bulk_invite(client: AsyncClient, admin_token: str, admin_user):
    role_map = admin_user._test_role_map
    response = await client.post(
        "/api/users/bulk-invite",
        json={
            "invites": [
                {"email": "bulk1@test.com", "role_id": role_map["bench"]},
                {"email": "bulk2@test.com", "role_id": role_map["viewer"]},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_invited"] == 2


@pytest.mark.asyncio
async def test_update_user_role(client: AsyncClient, admin_token: str, admin_user, viewer_user):
    role_map = admin_user._test_role_map
    response = await client.patch(
        f"/api/users/{viewer_user.id}",
        json={
            "role_id": role_map["comp_bio"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["role_name"] == "comp_bio"


@pytest.mark.asyncio
async def test_deactivate_user(client: AsyncClient, admin_token: str, viewer_user):
    response = await client.post(
        f"/api/users/{viewer_user.id}/deactivate",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "deactivated"


@pytest.mark.asyncio
async def test_cannot_deactivate_self(client: AsyncClient, admin_token: str, admin_user):
    response = await client.post(
        f"/api/users/{admin_user.id}/deactivate",
        headers={
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_viewer_cannot_list_users(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/users",
        headers={
            "Authorization": f"Bearer {viewer_token}",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_accept_invite(client: AsyncClient, admin_token: str, admin_user):
    role_map = admin_user._test_role_map
    resp = await client.post(
        "/api/users",
        json={
            "email": "invited@test.com",
            "role_id": role_map["bench"],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    from app.services.auth_service import AuthService

    user_id = resp.json()["id"]
    invite_token = AuthService.generate_invite_token(user_id, "invited@test.com")

    resp = await client.post(
        "/api/users/accept-invite",
        json={
            "token": invite_token,
            "password": "newuserpassword",
            "name": "New User",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()
