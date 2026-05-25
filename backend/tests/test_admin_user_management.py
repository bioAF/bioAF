import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_resend_invite(client: AsyncClient, admin_token: str, admin_user):
    """Admin can resend invitation to a user who has never logged in."""
    role_map = admin_user._test_role_map
    resp = await client.post(
        "/api/users",
        json={"email": "pending@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]

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
async def test_admin_reset_password_send_email(client: AsyncClient, admin_token: str, viewer_user, monkeypatch):
    """Admin can trigger a password reset email when the email actually sends."""
    from app.services.email_service import EmailService

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(lambda *a, **k: True))

    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert "reset" in resp.json()["message"].lower()


@pytest.mark.asyncio
async def test_admin_reset_email_reports_send_failure(client: AsyncClient, admin_token: str, viewer_user, monkeypatch):
    """If the email cannot be sent (e.g. bad SMTP creds), the admin gets an error, not a false success."""
    from app.services.email_service import EmailService

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(lambda *a, **k: False))

    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 502
    assert "sent" not in resp.json()["detail"].lower() or "could not" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_reset_email_link_is_absolute(client: AsyncClient, admin_token: str, viewer_user, monkeypatch):
    """The reset link in the email is an absolute URL, not a host-less path."""
    from app.services.email_service import EmailService

    captured: dict = {}

    def fake_send(to, code, reset_link):
        captured["link"] = reset_link
        return True

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(fake_send))

    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert captured["link"].startswith("http://test/reset-password?token=")


@pytest.mark.asyncio
async def test_admin_reset_password_set_temp(client: AsyncClient, admin_token: str, viewer_user):
    """Admin can set a temporary password for a user."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/admin-reset-password",
        json={"mode": "temporary", "temporary_password": "tempPass123!"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

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
    resp = await client.post(
        f"/api/users/{admin_user.id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_last_admin_guard_role_change(client: AsyncClient, admin_token: str, admin_user):
    """Cannot change role of the last active admin to non-admin."""
    role_map = admin_user._test_role_map
    resp = await client.patch(
        f"/api/users/{admin_user.id}",
        json={"role_id": role_map["viewer"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "last" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reactivate_user(client: AsyncClient, admin_token: str, viewer_user):
    """Admin can reactivate a deactivated user."""
    # Deactivate first
    resp = await client.post(
        f"/api/users/{viewer_user.id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"

    # Reactivate
    resp = await client.post(
        f"/api/users/{viewer_user.id}/reactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_reactivate_non_deactivated_user_fails(client: AsyncClient, admin_token: str, viewer_user):
    """Cannot reactivate a user that is not deactivated."""
    resp = await client.post(
        f"/api/users/{viewer_user.id}/reactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_deactivated_never_logged_in_user(client: AsyncClient, admin_token: str, admin_user):
    """Can delete a deactivated user who never logged in and has no audit trail as actor."""
    role_map = admin_user._test_role_map
    # Invite a user (never logs in)
    resp = await client.post(
        "/api/users",
        json={"email": "deleteme@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    # Deactivate
    resp = await client.post(
        f"/api/users/{user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Delete
    resp = await client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "User deleted"

    # Verify gone
    resp = await client.get(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_active_user_fails(client: AsyncClient, admin_token: str, viewer_user):
    """Cannot delete an active user."""
    resp = await client.delete(
        f"/api/users/{viewer_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "deactivated" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_user_with_audit_activity_fails(client: AsyncClient, admin_token: str, admin_user, session):
    """Cannot delete a user who has performed actions recorded in the audit log."""
    role_map = admin_user._test_role_map
    # Invite and accept (this creates audit entries where user is the actor)
    resp = await client.post(
        "/api/users",
        json={"email": "audited@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    from app.services.auth_service import AuthService

    invite_token = AuthService.generate_invite_token(user_id, "audited@test.com")
    resp = await client.post(
        "/api/users/accept-invite",
        json={"token": invite_token, "password": "newpass123", "name": "Audited"},
    )
    assert resp.status_code == 200

    # Deactivate
    resp = await client.post(
        f"/api/users/{user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # Try to delete -- should fail because user accepted invite (has audit activity as actor)
    resp = await client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
    assert "audit" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_user_with_pending_reset_code_succeeds(
    client: AsyncClient, admin_token: str, admin_user, monkeypatch
):
    """A deactivated, never-logged-in user can be deleted even after an admin sent them a
    password reset, which leaves a pending verification code referencing the user."""
    from app.services.email_service import EmailService

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(lambda *a, **k: True))

    role_map = admin_user._test_role_map
    resp = await client.post(
        "/api/users",
        json={"email": "resetdelete@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    user_id = resp.json()["id"]

    # Admin sends a password reset email -> creates a verification_codes row for the user.
    resp = await client.post(
        f"/api/users/{user_id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        f"/api/users/{user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    # The pending reset code must not block deletion. The user never acted.
    resp = await client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "User deleted"

    resp = await client.get(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_preserves_admin_reset_audit_entry(client: AsyncClient, admin_token: str, admin_user, monkeypatch):
    """Deleting the user discards their reset code but leaves the admin's reset audit entry."""
    from app.services.email_service import EmailService

    monkeypatch.setattr(EmailService, "send_password_reset", staticmethod(lambda *a, **k: True))

    role_map = admin_user._test_role_map
    resp = await client.post(
        "/api/users",
        json={"email": "audittrail@test.com", "role_id": role_map["bench"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_id = resp.json()["id"]

    await client.post(
        f"/api/users/{user_id}/admin-reset-password",
        json={"mode": "email"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        f"/api/users/{user_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp = await client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/api/audit?action=admin_reset_password",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    entity_ids = [e["entity_id"] for e in resp.json()["entries"]]
    assert user_id in entity_ids


@pytest.mark.asyncio
async def test_change_own_password(client: AsyncClient, admin_token: str, admin_user):
    """User can change their own platform password."""
    resp = await client.post(
        "/api/auth/me/change-password",
        json={"current_password": "testpassword123", "new_password": "newpass456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "testpassword123"},
    )
    assert resp.status_code == 401

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
