"""Tests for PlatformConfigService and its sensitive-key encryption.

PlatformConfig is a key-value table. Most keys (project_id, region,
schedule cron, etc.) are not sensitive. A small subset (currently just
gcp_service_account_key) must be encrypted at rest. This service is the
single read/write point for those sensitive keys so the encrypt/decrypt
boundary cannot be accidentally bypassed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import encryption_service
from app.services.platform_config_service import (
    SENSITIVE_PLATFORM_CONFIG_KEYS,
    PlatformConfigService,
)


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def test_sensitive_keys_includes_service_account_key():
    assert "gcp_service_account_key" in SENSITIVE_PLATFORM_CONFIG_KEYS


@pytest.mark.asyncio
async def test_set_then_get_round_trips_non_sensitive_key(db_engine):
    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_project_id", "my-project")
        await session.commit()

        assert await PlatformConfigService.get(session, "gcp_project_id") == "my-project"

        raw = (
            await session.execute(
                sa_text("SELECT value FROM platform_config WHERE key=:k"),
                {"k": "gcp_project_id"},
            )
        ).scalar_one()
        assert raw == "my-project"


@pytest.mark.asyncio
async def test_sensitive_key_is_encrypted_on_disk(db_engine):
    sa_key = "-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END"

    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_service_account_key", sa_key)
        await session.commit()

        raw = (
            await session.execute(sa_text("SELECT value FROM platform_config WHERE key='gcp_service_account_key'"))
        ).scalar_one()
        assert raw != sa_key
        assert raw.startswith("gAAAA")
        assert encryption_service.decrypt(raw) == sa_key

        # Service.get transparently decrypts.
        assert await PlatformConfigService.get(session, "gcp_service_account_key") == sa_key


@pytest.mark.asyncio
async def test_get_missing_key_returns_none(db_engine):
    async with _factory(db_engine)() as session:
        assert await PlatformConfigService.get(session, "no_such_key") is None


@pytest.mark.asyncio
async def test_set_overwrites_existing_value(db_engine):
    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_service_account_key", "first")
        await session.commit()
        await PlatformConfigService.set(session, "gcp_service_account_key", "second")
        await session.commit()

        assert await PlatformConfigService.get(session, "gcp_service_account_key") == "second"


@pytest.mark.asyncio
async def test_get_many_decrypts_only_sensitive_keys(db_engine):
    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_project_id", "proj-1")
        await PlatformConfigService.set(session, "gcp_service_account_key", "sa-key-XYZ")
        await session.commit()

        values = await PlatformConfigService.get_many(
            session, ["gcp_project_id", "gcp_service_account_key", "missing_key"]
        )
        assert values == {"gcp_project_id": "proj-1", "gcp_service_account_key": "sa-key-XYZ"}


@pytest.mark.asyncio
async def test_set_none_clears_value(db_engine):
    async with _factory(db_engine)() as session:
        await PlatformConfigService.set(session, "gcp_service_account_key", "x")
        await session.commit()
        await PlatformConfigService.set(session, "gcp_service_account_key", None)
        await session.commit()
        # Stored as NULL (not present in result) or empty string is fine; the
        # contract is "get() returns None".
        assert await PlatformConfigService.get(session, "gcp_service_account_key") in (None, "")
