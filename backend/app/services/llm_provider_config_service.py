"""Per-org LLM provider configuration service (ADR-053).

Stores up to one row per (organization_id, provider). Partial unique index on
the table guarantees at most one is_active=true row per org.

API keys are encrypted at rest via EncryptedString (ADR-047). The last 5 chars
of the secret are stored plaintext in api_key_prefix_last5 so audit rows can
identify which key authenticated a call without ever decrypting the column.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_provider_config import LlmProviderConfig
from app.services import audit_service

SUPPORTED_PROVIDERS = {"openai", "anthropic", "google", "gemma"}
HOSTED_PROVIDERS = {"openai", "anthropic", "google"}


def _prefix_last5(api_key: str | None) -> str | None:
    if not api_key:
        return None
    return api_key[-5:]


async def list_for_org(session: AsyncSession, org_id: int) -> Sequence[LlmProviderConfig]:
    result = await session.execute(
        select(LlmProviderConfig)
        .where(LlmProviderConfig.organization_id == org_id)
        .order_by(LlmProviderConfig.provider)
    )
    return result.scalars().all()


async def get_for_provider(session: AsyncSession, org_id: int, provider: str) -> LlmProviderConfig | None:
    result = await session.execute(
        select(LlmProviderConfig).where(
            LlmProviderConfig.organization_id == org_id,
            LlmProviderConfig.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def get_active(session: AsyncSession, org_id: int) -> LlmProviderConfig | None:
    result = await session.execute(
        select(LlmProviderConfig).where(
            LlmProviderConfig.organization_id == org_id,
            LlmProviderConfig.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def upsert(
    session: AsyncSession,
    org_id: int,
    provider: str,
    api_key: str | None,
    model: str | None,
    actor_user_id: int,
) -> LlmProviderConfig:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if provider in HOSTED_PROVIDERS and not api_key:
        raise ValueError(f"provider {provider} requires an api_key")

    existing = await get_for_provider(session, org_id, provider)
    is_first_save = existing is None
    previous_prefix = existing.api_key_prefix_last5 if existing else None
    new_prefix = _prefix_last5(api_key)

    if existing is None:
        row = LlmProviderConfig(
            organization_id=org_id,
            provider=provider,
            api_key=api_key,
            api_key_prefix_last5=new_prefix,
            model=model,
            is_active=False,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        session.add(row)
        await session.flush()
    else:
        row = existing
        row.api_key = api_key
        row.api_key_prefix_last5 = new_prefix
        row.model = model
        row.updated_by_user_id = actor_user_id
        await session.flush()

    if provider in HOSTED_PROVIDERS:
        await audit_service.log_action(
            session,
            user_id=actor_user_id,
            entity_type="llm_provider_config",
            entity_id=row.id,
            action="llm_provider_key_rotated",
            details={
                "provider": provider,
                "previous_api_key_prefix_last5": previous_prefix if not is_first_save else None,
                "new_api_key_prefix_last5": new_prefix,
            },
        )
    return row


async def set_active(session: AsyncSession, org_id: int, provider: str, actor_user_id: int) -> LlmProviderConfig:
    target = await get_for_provider(session, org_id, provider)
    if target is None:
        raise ValueError(f"no config row for provider {provider} in org {org_id}")
    # Deactivate all rows first to avoid hitting the partial unique index in
    # the same transaction; then activate the chosen row.
    await session.execute(
        update(LlmProviderConfig)
        .where(
            LlmProviderConfig.organization_id == org_id,
            LlmProviderConfig.is_active.is_(True),
        )
        .values(is_active=False, updated_by_user_id=actor_user_id)
    )
    target.is_active = True
    target.updated_by_user_id = actor_user_id
    await session.flush()
    await audit_service.log_action(
        session,
        user_id=actor_user_id,
        entity_type="llm_provider_config",
        entity_id=target.id,
        action="llm_provider_enabled",
        details={
            "provider": provider,
            "model": target.model,
            "api_key_prefix_last5": target.api_key_prefix_last5,
        },
    )
    return target


async def deactivate_all(session: AsyncSession, org_id: int, actor_user_id: int) -> None:
    previous = await get_active(session, org_id)
    await session.execute(
        update(LlmProviderConfig)
        .where(
            LlmProviderConfig.organization_id == org_id,
            LlmProviderConfig.is_active.is_(True),
        )
        .values(is_active=False, updated_by_user_id=actor_user_id)
    )
    await session.flush()
    if previous is not None:
        await audit_service.log_action(
            session,
            user_id=actor_user_id,
            entity_type="llm_provider_config",
            entity_id=previous.id,
            action="llm_provider_disabled",
            details={"previous_active_provider": previous.provider},
        )


async def delete(session: AsyncSession, org_id: int, provider: str, actor_user_id: int) -> None:
    row = await get_for_provider(session, org_id, provider)
    if row is None:
        return
    await session.delete(row)
    await session.flush()
