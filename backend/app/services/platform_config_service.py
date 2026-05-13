"""Centralized accessor for the platform_config key-value table.

Most platform_config keys are non-sensitive (project id, region, schedules,
etc.) and can be read or written via raw SQL anywhere. A small allow-list of
keys (currently just `gcp_service_account_key`) is sensitive and must be
encrypted at rest. Routing those keys through this service is the only way
to keep the encrypt/decrypt boundary intact.

When you add a new sensitive entry to platform_config, append it to
SENSITIVE_PLATFORM_CONFIG_KEYS and route the relevant caller(s) through
get / set / get_many.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import encryption_service

SENSITIVE_PLATFORM_CONFIG_KEYS: frozenset[str] = frozenset({"gcp_service_account_key"})


class PlatformConfigService:
    @staticmethod
    def _maybe_encrypt(key: str, value: str | None) -> str | None:
        if value is None:
            return None
        if key in SENSITIVE_PLATFORM_CONFIG_KEYS:
            return encryption_service.encrypt(value)
        return value

    @staticmethod
    def _maybe_decrypt(key: str, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        if key in SENSITIVE_PLATFORM_CONFIG_KEYS and encryption_service.looks_like_ciphertext(value):
            return encryption_service.decrypt(value)
        return value

    @staticmethod
    async def get(session: AsyncSession, key: str) -> str | None:
        row = (
            await session.execute(
                text("SELECT value FROM platform_config WHERE key = :k"),
                {"k": key},
            )
        ).scalar_one_or_none()
        return PlatformConfigService._maybe_decrypt(key, row)

    @staticmethod
    async def get_many(session: AsyncSession, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        rows = (
            await session.execute(
                text("SELECT key, value FROM platform_config WHERE key = ANY(:keys)").bindparams(keys=list(keys))
            )
        ).all()
        out: dict[str, str] = {}
        for k, v in rows:
            decrypted = PlatformConfigService._maybe_decrypt(k, v)
            if decrypted is not None:
                out[k] = decrypted
        return out

    @staticmethod
    async def set(session: AsyncSession, key: str, value: str | None) -> None:
        stored = PlatformConfigService._maybe_encrypt(key, value)
        if stored is None:
            await session.execute(
                text("DELETE FROM platform_config WHERE key = :k"),
                {"k": key},
            )
            return
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = now()"
            ),
            {"k": key, "v": stored},
        )
