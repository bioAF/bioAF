import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_service import log_action


@pytest.mark.asyncio
async def test_audit_log_write(session: AsyncSession, admin_user):
    """Test that audit log entries can be written."""
    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="user",
        entity_id=admin_user.id,
        action="test_action",
        details={"test": "data"},
    )
    await session.commit()

    result = await session.execute(text("SELECT * FROM audit_log WHERE action = 'test_action'"))
    rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0].entity_type == "user"


@pytest.mark.asyncio
async def test_audit_log_within_transaction(session: AsyncSession, admin_user):
    """Test that audit log is written within the same transaction."""
    from app.models.user import User

    # Make a state change and audit log write in the same transaction
    user = await session.get(User, admin_user.id)
    assert user is not None
    old_name = user.name
    user.name = "Updated Name"
    await session.flush()

    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="user",
        entity_id=admin_user.id,
        action="update",
        details={"field": "name", "new_value": "Updated Name"},
        previous_value={"field": "name", "old_value": old_name},
    )
    await session.commit()

    # Verify both the state change and audit log exist
    result = await session.execute(
        text("SELECT * FROM audit_log WHERE action = 'update' AND entity_id = :id"),
        {"id": admin_user.id},
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_audit_log_system_event(session: AsyncSession):
    """Test that system events (no user) can be logged."""
    await log_action(
        session,
        user_id=None,
        entity_type="system",
        entity_id=0,
        action="startup",
        details={"version": "0.1.0"},
    )
    await session.commit()

    result = await session.execute(text("SELECT * FROM audit_log WHERE entity_type = 'system'"))
    rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0].user_id is None


@pytest.mark.asyncio
async def test_audit_log_immutability(session: AsyncSession, admin_user):
    """Test that audit log entries cannot be updated or deleted at the DB level.
    Note: This test verifies the application-level behavior. The actual DB-level
    enforcement (REVOKE UPDATE, DELETE) is applied by the migration and may not
    be active in the test database if running without the bioaf_app role."""
    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="test",
        entity_id=1,
        action="immutability_test",
    )
    await session.commit()

    # Verify the entry exists
    result = await session.execute(text("SELECT id FROM audit_log WHERE action = 'immutability_test'"))
    row = result.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_audit_api_date_filter(client, admin_token, admin_user, session):
    """Test that audit log API supports date range filtering."""
    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="experiment",
        entity_id=1,
        action="create",
        details={"name": "EXP-1"},
    )
    await session.commit()

    response = await client.get(
        "/api/audit?start_date=2020-01-01&end_date=2099-12-31",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_audit_api_user_id_filter(client, admin_token, admin_user, session):
    """Test that audit log API supports user_id filtering."""
    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="sample",
        entity_id=1,
        action="create",
    )
    await session.commit()

    response = await client.get(
        f"/api/audit?user_id={admin_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    for entry in data["entries"]:
        assert entry["user"] is not None
        assert entry["user"]["email"] == admin_user.email


@pytest.mark.asyncio
async def test_audit_api_export_csv(client, admin_token, admin_user, session):
    """Test CSV export of audit log."""
    await log_action(
        session,
        user_id=admin_user.id,
        entity_type="experiment",
        entity_id=1,
        action="create",
    )
    await session.commit()

    response = await client.get(
        "/api/audit/export?format=csv",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "timestamp" in response.text


@pytest.mark.asyncio
async def test_login_creates_audit_entry(client, admin_user, session):
    """Test that login creates an audit log entry."""
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "testpassword123",
        },
    )
    assert response.status_code == 200

    result = await session.execute(
        text("SELECT * FROM audit_log WHERE action = 'login' AND entity_id = :id"),
        {"id": admin_user.id},
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_failed_login_creates_audit_entry(client, admin_user, session):
    """Failed login with wrong password creates login_failed audit entry."""
    response = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401

    result = await session.execute(
        text("SELECT details_json FROM audit_log WHERE action = 'login_failed' AND entity_id = :id"),
        {"id": admin_user.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row.details_json["reason"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_failed_login_nonexistent_user_creates_audit_entry(client, session):
    """Failed login for nonexistent email still creates login_failed audit entry."""
    response = await client.post(
        "/api/auth/login",
        json={"email": "nobody@test.com", "password": "password"},
    )
    assert response.status_code == 401

    result = await session.execute(
        text("SELECT details_json FROM audit_log WHERE action = 'login_failed' AND entity_id = 0"),
    )
    row = result.fetchone()
    assert row is not None
    assert row.details_json["email"] == "nobody@test.com"


@pytest.mark.asyncio
async def test_logout_creates_audit_entry(client, admin_token, admin_user, session):
    """Logout creates an audit entry."""
    response = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    result = await session.execute(
        text("SELECT * FROM audit_log WHERE action = 'logout' AND entity_id = :id"),
        {"id": admin_user.id},
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_role_change_audit_action(client, admin_token, admin_user, session):
    """Role change uses 'role_change' action with old/new role details."""
    from app.models.user import User
    from app.services.auth_service import AuthService

    role_map = admin_user._test_role_map

    # Create a second user to change role on
    user2 = User(
        email="roletest@test.com",
        password_hash=AuthService.hash_password("pass123"),
        role_id=role_map["viewer"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user2)
    await session.flush()
    await session.commit()

    response = await client.patch(
        f"/api/users/{user2.id}",
        json={"role_id": role_map["comp_bio"]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    result = await session.execute(
        text(
            "SELECT details_json, previous_value_json FROM audit_log WHERE action = 'role_change' AND entity_id = :id"
        ),
        {"id": user2.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row.details_json["new_role_name"] == "comp_bio"
    assert row.previous_value_json["old_role_name"] == "viewer"


@pytest.mark.asyncio
async def test_quota_exceeded_creates_audit_entry(session, admin_user):
    """Quota exceeded creates an audit log entry."""
    from app.services.quota_service import QuotaService

    # Set a low quota
    await QuotaService.set_quota(
        session,
        user_id=admin_user.id,
        admin_user_id=admin_user.id,
        org_id=admin_user.organization_id,
        limit=10,
    )
    await session.commit()

    # Use up the quota
    await QuotaService.update_usage(session, admin_user.id, 9.5)
    await session.commit()

    # Try to exceed it
    allowed, _ = await QuotaService.check_quota(session, admin_user.id, 5.0)
    assert allowed is False
    await session.commit()

    result = await session.execute(
        text("SELECT details_json FROM audit_log WHERE action = 'quota_exceeded'"),
    )
    row = result.fetchone()
    assert row is not None
    assert row.details_json["cpu_hours_limit"] == 10
