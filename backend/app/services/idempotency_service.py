"""Idempotency-Key replay (ADR-050).

Caches the response of a write request keyed by (api_key_id, key) and replays
the cached body on subsequent requests carrying the same Idempotency-Key.

Retention: 24h after creation. Cleanup runs from the main app background loop.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency_key import IdempotencyKey

TTL_HOURS = 24


def fingerprint(method: str, path: str, body: Any) -> str:
    """sha256 hash of method + path + canonical-JSON body. Used to detect
    same-key-but-different-body collisions (ADR-050)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    raw = f"{method.upper()}\n{path}\n{canonical}".encode()
    return hashlib.sha256(raw).hexdigest()


async def lookup(session: AsyncSession, api_key_id: int, key: str) -> IdempotencyKey | None:
    stmt = select(IdempotencyKey).where(IdempotencyKey.api_key_id == api_key_id, IdempotencyKey.key == key)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == row.id))
        await session.flush()
        return None
    return row


async def record(
    session: AsyncSession,
    api_key_id: int,
    key: str,
    request_fingerprint: str,
    response_status: int,
    response_body: dict | None,
) -> IdempotencyKey:
    now = datetime.now(timezone.utc)
    row = IdempotencyKey(
        api_key_id=api_key_id,
        key=key,
        request_fingerprint=request_fingerprint,
        response_status=response_status,
        response_body=response_body,
        expires_at=now + timedelta(hours=TTL_HOURS),
    )
    session.add(row)
    await session.flush()
    return row


async def cleanup_expired(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at <= now))
    return result.rowcount or 0
