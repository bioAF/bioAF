"""Service-account User row management. SAs are User rows with
is_service_account=true, a synthetic email, no usable password hash, and a
display name shown in the admin UI."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.user import User
from app.services import audit_service

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "sa"


def _synthetic_email(display_name: str, org_id: int) -> str:
    """Build a non-routable synthetic email. Avoids reserved TLDs (.local,
    .test, .invalid) that pydantic EmailStr rejects."""
    slug = _slugify(display_name)
    suffix = secrets.token_hex(4)
    return f"sa-{slug}-{suffix}@org{org_id}.bioaf.svc"


async def create(
    session: AsyncSession,
    org_id: int,
    display_name: str,
    role_id: int,
    created_by_user_id: int,
) -> User:
    """Insert a new service-account User row. No password is set; the password
    hash is a non-bcrypt sentinel value that cannot match any presented password."""
    email = _synthetic_email(display_name, org_id)
    user = User(
        organization_id=org_id,
        email=email,
        name=display_name,
        display_name=display_name,
        # Sentinel that bcrypt.checkpw will reject as malformed, so even if
        # auth_service ever reaches verify_password for an SA, it cannot match.
        password_hash="!service-account-no-password!",
        role_id=role_id,
        status="active",
        is_service_account=True,
    )
    session.add(user)
    await session.flush()

    await audit_service.log_action(
        session,
        user_id=created_by_user_id,
        entity_type="service_account",
        entity_id=user.id,
        action="create",
        details={"display_name": display_name, "role_id": role_id},
    )
    return user


async def list_for_org(session: AsyncSession, org_id: int) -> list[User]:
    stmt = (
        select(User)
        .where(User.organization_id == org_id)
        .where(User.is_service_account.is_(True))
        .order_by(User.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_role(session: AsyncSession, sa_user_id: int, role_id: int, actor_user_id: int) -> User:
    result = await session.execute(select(User).where(User.id == sa_user_id))
    row = result.scalar_one_or_none()
    if row is None or not row.is_service_account:
        raise LookupError(f"service_account {sa_user_id} not found")
    previous = row.role_id
    row.role_id = role_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="service_account",
        entity_id=row.id,
        action="update_role",
        details={"role_id": role_id},
        previous_value={"role_id": previous},
    )
    return row


async def update_display_name(session: AsyncSession, sa_user_id: int, display_name: str, actor_user_id: int) -> User:
    result = await session.execute(select(User).where(User.id == sa_user_id))
    row = result.scalar_one_or_none()
    if row is None or not row.is_service_account:
        raise LookupError(f"service_account {sa_user_id} not found")
    previous = row.display_name
    row.display_name = display_name
    row.name = display_name
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="service_account",
        entity_id=row.id,
        action="update_display_name",
        details={"display_name": display_name},
        previous_value={"display_name": previous},
    )
    return row


async def disable(session: AsyncSession, sa_user_id: int, actor_user_id: int) -> User:
    """Soft-disable an SA. Marks the user inactive and revokes every key
    belonging to it."""
    result = await session.execute(select(User).where(User.id == sa_user_id))
    row = result.scalar_one_or_none()
    if row is None or not row.is_service_account:
        raise LookupError(f"service_account {sa_user_id} not found")
    row.status = "disabled"

    keys = await session.execute(select(ApiKey).where(ApiKey.service_account_user_id == sa_user_id))
    now = datetime.now(timezone.utc)
    revoked_key_ids: list[int] = []
    for key in keys.scalars():
        if key.revoked_at is None:
            key.revoked_at = now
            revoked_key_ids.append(key.id)
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="service_account",
        entity_id=row.id,
        action="disable",
        details={"revoked_key_ids": revoked_key_ids},
    )
    return row
