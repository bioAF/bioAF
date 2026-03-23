import pytest
import pytest_asyncio
from decimal import Decimal
from datetime import datetime, timezone

from app.services.auth_service import AuthService


@pytest_asyncio.fixture
async def comp_bio_user(session, admin_user):
    from app.models.user import User

    password_hash = AuthService.hash_password("compbiopass123")
    user = User(
        email="compbio@test.com",
        password_hash=password_hash,
        role_id=admin_user._test_role_map["comp_bio"],
        organization_id=admin_user.organization_id,
        status="active",
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest_asyncio.fixture
async def comp_bio_token(comp_bio_user) -> str:
    return AuthService.create_token(
        comp_bio_user.id,
        comp_bio_user.email,
        comp_bio_user.role_id,
        comp_bio_user.organization_id,
        role_name="comp_bio",
    )


@pytest.mark.asyncio
async def test_admin_set_quota(client, admin_token, comp_bio_user):
    """Admin can set quota for any user."""
    response = await client.patch(
        f"/api/quotas/{comp_bio_user.id}",
        json={"cpu_hours_monthly_limit": 100},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cpu_hours_limit"] == 100
    assert data["user_id"] == comp_bio_user.id


@pytest.mark.asyncio
async def test_quota_check_passes_under_limit(session, comp_bio_user):
    """Quota check passes when under limit."""
    from app.models.user_quota import UserQuota
    from app.services.quota_service import QuotaService

    quota = UserQuota(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        cpu_hours_monthly_limit=100,
        cpu_hours_used_current_month=Decimal("50.0"),
        quota_reset_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    session.add(quota)
    await session.flush()
    await session.commit()

    allowed, message = await QuotaService.check_quota(session, comp_bio_user.id, estimated_hours=10.0)
    assert allowed is True


@pytest.mark.asyncio
async def test_quota_check_fails_over_limit(session, comp_bio_user):
    """Quota check fails when over limit."""
    from app.models.user_quota import UserQuota
    from app.services.quota_service import QuotaService

    quota = UserQuota(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        cpu_hours_monthly_limit=100,
        cpu_hours_used_current_month=Decimal("95.0"),
        quota_reset_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    session.add(quota)
    await session.flush()
    await session.commit()

    allowed, message = await QuotaService.check_quota(session, comp_bio_user.id, estimated_hours=10.0)
    assert allowed is False
    assert "exceed" in message.lower()


@pytest.mark.asyncio
async def test_null_limit_means_unlimited(session, comp_bio_user):
    """NULL limit means unlimited quota."""
    from app.models.user_quota import UserQuota
    from app.services.quota_service import QuotaService

    quota = UserQuota(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        cpu_hours_monthly_limit=None,
        cpu_hours_used_current_month=Decimal("9999.0"),
        quota_reset_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    session.add(quota)
    await session.flush()
    await session.commit()

    allowed, message = await QuotaService.check_quota(session, comp_bio_user.id, estimated_hours=100.0)
    assert allowed is True
    assert "unlimited" in message.lower()


@pytest.mark.asyncio
async def test_monthly_reset_zeroes_usage(session, comp_bio_user):
    """Monthly reset zeroes usage."""
    from app.models.user_quota import UserQuota
    from app.services.quota_service import QuotaService

    # Set quota with reset date in the past
    quota = UserQuota(
        user_id=comp_bio_user.id,
        organization_id=comp_bio_user.organization_id,
        cpu_hours_monthly_limit=100,
        cpu_hours_used_current_month=Decimal("75.0"),
        quota_reset_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    session.add(quota)
    await session.flush()
    await session.commit()

    # get_quota triggers reset
    updated_quota = await QuotaService.get_quota(session, comp_bio_user.id)
    assert float(updated_quota.cpu_hours_used_current_month) == 0.0


@pytest.mark.asyncio
async def test_non_admin_cannot_modify_quotas(client, comp_bio_token, comp_bio_user):
    """Non-admin cannot modify quotas."""
    response = await client.patch(
        f"/api/quotas/{comp_bio_user.id}",
        json={"cpu_hours_monthly_limit": 100},
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_comp_bio_can_read_own_quota(client, comp_bio_token):
    """Comp Bio can read own quota."""
    response = await client.get(
        "/api/quotas/me",
        headers={"Authorization": f"Bearer {comp_bio_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "cpu_hours_used" in data


@pytest.mark.asyncio
async def test_set_quota_creates_audit_entry(client, admin_token, comp_bio_user, session):
    """Setting quota writes an audit log entry."""
    response = await client.patch(
        f"/api/quotas/{comp_bio_user.id}",
        json={"cpu_hours_monthly_limit": 200},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    from sqlalchemy import select
    from app.models.audit_log import AuditLog

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "user_quota",
            AuditLog.action == "set_quota",
        )
    )
    entries = list(result.scalars().all())
    assert len(entries) >= 1


@pytest.mark.asyncio
async def test_admin_list_quotas(client, admin_token):
    """Admin can list all quotas."""
    response = await client.get(
        "/api/quotas",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)
