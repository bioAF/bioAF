"""Service-account API key minting, verification, and revocation."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.services import audit_service
from app.services.bootstrap_roles import ALL_RESOURCES_ACTIONS

KEY_PREFIX = "biokey_"
_PREFIX_LEN = 12
_SECRET_LEN = 32
_ALPHABET = string.ascii_letters + string.digits

# Scope alphabet exposed via the public integration API (v1). Strictly a subset
# of the role-permission registry. Validated at mint-time.
PUBLIC_SCOPE_ALPHABET: frozenset[str] = frozenset(
    {
        "projects:view",
        "projects:create",
        "projects:edit",
        "experiments:view",
        "experiments:create",
        "experiments:edit",
        "samples:view",
        "samples:create",
        "samples:edit",
        "files:view",
    }
)


def _all_registry_pairs() -> frozenset[str]:
    return frozenset(
        f"{resource}:{action}" for resource, actions in ALL_RESOURCES_ACTIONS.items() for action in actions
    )


def _generate_prefix() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_PREFIX_LEN))


def _generate_secret() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_SECRET_LEN))


def _hash(presented: str) -> str:
    return bcrypt.hashpw(presented.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_hash(presented: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(presented.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_scopes(scopes: list[str]) -> list[str]:
    """Return the input if every scope is in the public alphabet; otherwise raise."""
    invalid = [s for s in scopes if s not in PUBLIC_SCOPE_ALPHABET]
    if invalid:
        raise ValueError(f"Unknown scope(s): {', '.join(sorted(invalid))}")
    return scopes


async def mint(
    session: AsyncSession,
    org_id: int,
    sa_user_id: int,
    name: str,
    scopes: list[str],
    created_by_user_id: int,
) -> tuple[ApiKey, str]:
    """Mint a new API key. Returns (row, full_secret) where full_secret is
    shown to the operator exactly once."""
    validate_scopes(scopes)
    prefix = _generate_prefix()
    secret = _generate_secret()
    full = f"{KEY_PREFIX}{prefix}.{secret}"
    key_hash = _hash(full)

    row = ApiKey(
        organization_id=org_id,
        service_account_user_id=sa_user_id,
        name=name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=list(scopes),
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()

    await audit_service.log_action(
        session,
        user_id=created_by_user_id,
        entity_type="api_key",
        entity_id=row.id,
        action="mint",
        details={"name": name, "scopes": scopes, "service_account_user_id": sa_user_id},
    )

    return row, full


async def verify(session: AsyncSession, presented: str) -> ApiKey | None:
    """Look up an API key by its presented `biokey_<prefix>.<secret>` string.
    Returns the row if it exists, is not revoked, and the bcrypt comparison
    succeeds; otherwise None."""
    if not presented.startswith(KEY_PREFIX):
        return None
    body = presented[len(KEY_PREFIX) :]
    if "." not in body:
        return None
    prefix, _ = body.split(".", 1)
    if len(prefix) != _PREFIX_LEN:
        return None

    result = await session.execute(select(ApiKey).where(ApiKey.key_prefix == prefix))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if not _verify_hash(presented, row.key_hash):
        return None
    return row


async def revoke(session: AsyncSession, key_id: int, actor_user_id: int) -> ApiKey:
    """Mark an API key revoked. Idempotent."""
    result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise LookupError(f"api_key {key_id} not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        await session.flush()
        await audit_service.log_action(
            session,
            user_id=actor_user_id,
            entity_type="api_key",
            entity_id=row.id,
            action="revoke",
            details={"name": row.name, "service_account_user_id": row.service_account_user_id},
        )
    return row


async def list_for_org(session: AsyncSession, org_id: int, include_revoked: bool = False) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.organization_id == org_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    stmt = stmt.order_by(ApiKey.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_for_service_account(
    session: AsyncSession, sa_user_id: int, include_revoked: bool = True
) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.service_account_user_id == sa_user_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    stmt = stmt.order_by(ApiKey.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


# In-process debounce table for last_used_at updates. Only one write per minute
# per key, keyed by api_key_id. Lost on pod restart, which is fine: last_used_at
# is observability, not authorization.
_LAST_USED_DEBOUNCE: dict[int, datetime] = {}
_DEBOUNCE_INTERVAL_SECONDS = 60


async def touch_last_used(session: AsyncSession, key_id: int) -> None:
    now = datetime.now(timezone.utc)
    last = _LAST_USED_DEBOUNCE.get(key_id)
    if last is not None and (now - last).total_seconds() < _DEBOUNCE_INTERVAL_SECONDS:
        return
    _LAST_USED_DEBOUNCE[key_id] = now
    await session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=now))
