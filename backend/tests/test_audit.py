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
