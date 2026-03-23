import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_resend_invite(client: AsyncClient, admin_token: str):
    """Admin can resend invitation to a user who has never logged in."""
    # Invite a user
    resp = await client.post(
        "/api/users",
        json={"email": "pending@test.com", "role": "bench"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    # Resend invite
    resp = await client.post(
        f"/api/users/{user_id}/resend-invite",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Invitation resent"


@pytest.mark.asyncio
async def test_resend_invite_fails_for_active_user(client: AsyncClient, admin_token: str, viewer_user):
    """Cannot resend invite to an active user."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/resend-invite",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "invited" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_reset_password_send_email(client: AsyncClient, admin_token: str, viewer_user):
    """Admin can trigger a password reset email for a user."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert "reset" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_reset_password_set_temp(client: AsyncClient, admin_token: str, viewer_user):
    """Admin can set a temporary password for a user."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "temporary", "temporary_password": "tempPass123!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # User should be able to log in with temp password
    resp = await client.post(
        "/api/auth/login",
        json={"email": "viewer@test.com", "password": "tempPass123!"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_reset_password_temp_requires_password(client: AsyncClient, admin_token: str, viewer_user):
    """Temporary mode requires a password value."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "temporary"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_last_admin_guard_deactivate(client: AsyncClient, admin_token: str, admin_user):
    """Cannot deactivate the last active admin."""
    # admin_user is the only admin -- deactivating should fail
    resp = await client.post(
        f"/api/users/{admin_user.id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_last_admin_guard_role_change(client: AsyncClient, admin_token: str, admin_user):
    """Cannot change role of the last active admin to non-admin."""
    resp = await client.patch(
        f"/api/users/{admin_user.id}",
        json={"role": "viewer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "last" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_change_own_password(client: AsyncClient, admin_token: str, admin_user):
    """User can change their own platform password."""
    resp = await client.post(
        "/api/auth/me/change-password",
        json={"current_password": "testpassword123", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Old password should no longer work
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "testpassword123"},
    )
    assert resp.status_code == 401

    # New password should work
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "newpass456"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_change_own_password_wrong_current(client: AsyncClient, admin_token: str, admin_user):
    """Rejects password change when current password is wrong."""
    resp = await client.post(
        "/api/auth/me/change-password",
        json={"current_password": "wrongpass", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "current password" in resp.json()["detail"].lower()
