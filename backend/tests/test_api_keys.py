"""Tests for ADR-049: service accounts and API key authentication."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_key_service, service_account_service


@pytest.mark.asyncio
async def test_mint_returns_secret_once_and_stores_only_hash(session, admin_user):
    sa = await service_account_service.create(
        session,
        org_id=admin_user.organization_id,
        display_name="Benchling Sync",
        role_id=admin_user.role_id,
        created_by_user_id=admin_user.id,
    )

    row, full_secret = await api_key_service.mint(
        session,
        org_id=admin_user.organization_id,
        sa_user_id=sa.id,
        name="primary",
        scopes=["samples:view", "experiments:view"],
        created_by_user_id=admin_user.id,
    )
    await session.commit()

    assert full_secret.startswith("biokey_")
    assert "." in full_secret
    assert row.key_hash != full_secret
    # The plaintext secret is not stored anywhere on the row.
    assert full_secret not in row.key_hash
    assert row.scopes == ["samples:view", "experiments:view"]
    assert row.service_account_user_id == sa.id


@pytest.mark.asyncio
async def test_verify_happy_path(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    row, full = await api_key_service.mint(
        session,
        admin_user.organization_id,
        sa.id,
        "primary",
        ["files:view"],
        admin_user.id,
    )
    await session.commit()

    verified = await api_key_service.verify(session, full)
    assert verified is not None
    assert verified.id == row.id


@pytest.mark.asyncio
async def test_verify_rejects_revoked_key(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    row, full = await api_key_service.mint(session, admin_user.organization_id, sa.id, "primary", [], admin_user.id)
    await session.commit()
    await api_key_service.revoke(session, row.id, admin_user.id)
    await session.commit()

    assert await api_key_service.verify(session, full) is None


@pytest.mark.asyncio
async def test_verify_rejects_wrong_secret_with_right_prefix(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    row, full = await api_key_service.mint(session, admin_user.organization_id, sa.id, "primary", [], admin_user.id)
    await session.commit()
    prefix_part, _ = full.split(".", 1)
    tampered = f"{prefix_part}.{'x' * 32}"

    assert await api_key_service.verify(session, tampered) is None
    # And the legitimate key still works
    assert await api_key_service.verify(session, full) is not None


@pytest.mark.asyncio
async def test_verify_rejects_nonexistent_prefix(session, admin_user):
    bogus = "biokey_" + ("a" * 12) + "." + ("z" * 32)
    assert await api_key_service.verify(session, bogus) is None


@pytest.mark.asyncio
async def test_verify_rejects_malformed_token(session):
    assert await api_key_service.verify(session, "biokey_noseparator") is None
    assert await api_key_service.verify(session, "not_a_biokey_at_all") is None
    assert await api_key_service.verify(session, "biokey_short.x") is None


@pytest.mark.asyncio
async def test_mint_rejects_unknown_scope(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    with pytest.raises(ValueError):
        await api_key_service.mint(
            session,
            admin_user.organization_id,
            sa.id,
            "bad",
            ["finance:wire_transfer"],
            admin_user.id,
        )


@pytest.mark.asyncio
async def test_service_account_cannot_login_via_password(client, session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    await session.commit()

    resp = await client.post("/api/auth/login", json={"email": sa.email, "password": "any-password"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_rows_written_for_mint_and_revoke(session, admin_user):
    from app.models.audit_log import AuditLog

    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    row, _ = await api_key_service.mint(session, admin_user.organization_id, sa.id, "primary", [], admin_user.id)
    await session.commit()
    await api_key_service.revoke(session, row.id, admin_user.id)
    await session.commit()

    audits = (await session.execute(select(AuditLog).where(AuditLog.entity_type == "api_key"))).scalars().all()
    actions = {a.action for a in audits}
    assert "mint" in actions
    assert "revoke" in actions


@pytest.mark.asyncio
async def test_disable_service_account_revokes_all_keys(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    k1, _ = await api_key_service.mint(session, admin_user.organization_id, sa.id, "k1", [], admin_user.id)
    k2, _ = await api_key_service.mint(session, admin_user.organization_id, sa.id, "k2", [], admin_user.id)
    await session.commit()

    await service_account_service.disable(session, sa.id, admin_user.id)
    await session.commit()

    keys = await api_key_service.list_for_service_account(session, sa.id)
    assert all(k.revoked_at is not None for k in keys)

    sa_after = (await session.execute(select(User).where(User.id == sa.id))).scalar_one()
    assert sa_after.status == "disabled"


@pytest.mark.asyncio
async def test_service_account_user_row_marked(session, admin_user):
    sa = await service_account_service.create(
        session,
        admin_user.organization_id,
        "Benchling Sync",
        admin_user.role_id,
        admin_user.id,
    )
    await session.commit()
    assert sa.is_service_account is True
    assert sa.display_name == "Benchling Sync"
    assert sa.email.endswith("@org%d.bioaf.svc" % admin_user.organization_id)


@pytest.mark.asyncio
async def test_touch_last_used_writes_on_first_call(session, admin_user):
    sa = await service_account_service.create(
        session, admin_user.organization_id, "SA1", admin_user.role_id, admin_user.id
    )
    row, _ = await api_key_service.mint(session, admin_user.organization_id, sa.id, "k1", [], admin_user.id)
    await session.commit()
    api_key_service._LAST_USED_DEBOUNCE.pop(row.id, None)

    await api_key_service.touch_last_used(session, row.id)
    await session.commit()

    refreshed = (await session.execute(select(ApiKey).where(ApiKey.id == row.id))).scalar_one()
    assert refreshed.last_used_at is not None
    assert refreshed.last_used_at <= datetime.now(timezone.utc)
