"""Tests for the llm_provider_config table and service.

Covered:
- Upsert creates a row when none exists; updates when one does.
- api_key column is stored encrypted at rest.
- api_key_prefix_last5 is the last 5 chars of the original key.
- set_active flips the chosen row to active and others to inactive.
- Partial unique index prevents two active rows per org at the DB level.
- Gemma rows store no key but still take a model.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(db_engine, admin_user):
    from app.models.llm_provider_config import LlmProviderConfig
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        row = await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-abcdefghij-LAST5",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await session.commit()
        first_id = row.id

        row2 = await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-different-NEW55",
            model="gpt-5-mini",
            actor_user_id=admin_user.id,
        )
        await session.commit()
        assert row2.id == first_id
        assert row2.model == "gpt-5-mini"
        assert row2.api_key == "sk-different-NEW55"
        assert row2.api_key_prefix_last5 == "NEW55"

    async with _factory(db_engine)() as session:
        count = (
            await session.execute(
                select(LlmProviderConfig).where(
                    LlmProviderConfig.organization_id == admin_user.organization_id,
                    LlmProviderConfig.provider == "openai",
                )
            )
        ).scalars().all()
        assert len(count) == 1


@pytest.mark.asyncio
async def test_api_key_encrypted_at_rest(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="anthropic",
            api_key="sk-ant-secretvalue12345",
            model="claude-opus-4",
            actor_user_id=admin_user.id,
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        raw = (
            await session.execute(
                sa_text(
                    "SELECT api_key FROM llm_provider_config WHERE organization_id = :org AND provider = :p"
                ),
                {"org": admin_user.organization_id, "p": "anthropic"},
            )
        ).scalar_one()
        assert raw.startswith("gAAAA"), f"expected Fernet ciphertext, got {raw!r}"


@pytest.mark.asyncio
async def test_api_key_prefix_last5_stored_plaintext(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        row = await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-XXXyyyZZZ-abc12",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await session.commit()
        assert row.api_key_prefix_last5 == "abc12"

    async with _factory(db_engine)() as session:
        raw = (
            await session.execute(
                sa_text(
                    "SELECT api_key_prefix_last5 FROM llm_provider_config "
                    "WHERE organization_id = :org AND provider = :p"
                ),
                {"org": admin_user.organization_id, "p": "openai"},
            )
        ).scalar_one()
        assert raw == "abc12"


@pytest.mark.asyncio
async def test_gemma_row_has_no_key(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        row = await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="gemma",
            api_key=None,
            model="gemma-4-9b",
            actor_user_id=admin_user.id,
        )
        await session.commit()
        assert row.api_key is None
        assert row.api_key_prefix_last5 is None
        assert row.model == "gemma-4-9b"


@pytest.mark.asyncio
async def test_set_active_flips_singletons(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-openai-key01",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="anthropic",
            api_key="sk-anth-key0002",
            model="claude-opus-4",
            actor_user_id=admin_user.id,
        )
        await llm_provider_config_service.set_active(
            session, org_id=admin_user.organization_id, provider="openai", actor_user_id=admin_user.id
        )
        await session.commit()

        active = await llm_provider_config_service.get_active(session, admin_user.organization_id)
        assert active is not None
        assert active.provider == "openai"
        assert active.is_active is True

        await llm_provider_config_service.set_active(
            session, org_id=admin_user.organization_id, provider="anthropic", actor_user_id=admin_user.id
        )
        await session.commit()
        active2 = await llm_provider_config_service.get_active(session, admin_user.organization_id)
        assert active2 is not None
        assert active2.provider == "anthropic"


@pytest.mark.asyncio
async def test_partial_unique_index_at_db_level(db_engine, admin_user):
    """Manually flip two rows to is_active=true and confirm Postgres rejects it."""
    from app.models.llm_provider_config import LlmProviderConfig
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-openai-key01",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="anthropic",
            api_key="sk-anth-key0002",
            model="claude-opus-4",
            actor_user_id=admin_user.id,
        )
        await session.commit()

    async with _factory(db_engine)() as session:
        rows = (
            await session.execute(
                select(LlmProviderConfig).where(LlmProviderConfig.organization_id == admin_user.organization_id)
            )
        ).scalars().all()
        for r in rows:
            r.is_active = True
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_get_active_returns_none_when_no_provider_active(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-openai-key01",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await session.commit()
        # No call to set_active.
        active = await llm_provider_config_service.get_active(session, admin_user.organization_id)
        assert active is None


@pytest.mark.asyncio
async def test_deactivate_clears_all(db_engine, admin_user):
    from app.services import llm_provider_config_service

    async with _factory(db_engine)() as session:
        await llm_provider_config_service.upsert(
            session,
            org_id=admin_user.organization_id,
            provider="openai",
            api_key="sk-openai-key01",
            model="gpt-5",
            actor_user_id=admin_user.id,
        )
        await llm_provider_config_service.set_active(
            session, org_id=admin_user.organization_id, provider="openai", actor_user_id=admin_user.id
        )
        await session.commit()

        await llm_provider_config_service.deactivate_all(
            session, org_id=admin_user.organization_id, actor_user_id=admin_user.id
        )
        await session.commit()
        active = await llm_provider_config_service.get_active(session, admin_user.organization_id)
        assert active is None
