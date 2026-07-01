"""Tests for assistant availability (L4): is the assistant usable for this org?

The assistant rides on the org's active LLM provider (ADR-053), but unlike the advisory
agent-review job it needs native tool-calling. So availability is stricter: it is enabled
only when there is an active provider, with a model, whose provider is tool-capable
(anthropic / openai / google). Gemma is not tool-capable in v1, so an org on Gemma gets a
clear "pick a tool-capable provider" reason instead of a broken assistant. Mirrors
agent_reviews/availability.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services import llm_provider_config_service
from app.services.assistant_availability_service import AssistantAvailabilityService

pytestmark = pytest.mark.asyncio


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _configure_active_provider(session, org_id, user_id, *, provider, model="m1"):
    await llm_provider_config_service.upsert(
        session,
        org_id=org_id,
        provider=provider,
        api_key="sk-test-LAST5" if provider != "gemma" else None,
        model=model,
        actor_user_id=user_id,
    )
    await llm_provider_config_service.set_active(session, org_id=org_id, provider=provider, actor_user_id=user_id)
    await session.commit()


# ---- Service ----


async def test_unavailable_when_no_active_provider(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        avail = await AssistantAvailabilityService.get_availability(session, admin_user.organization_id)
        assert avail.enabled is False
        assert "provider" in avail.reason.lower()


async def test_unavailable_for_non_tool_capable_provider(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="gemma")
        avail = await AssistantAvailabilityService.get_availability(session, admin_user.organization_id)
        assert avail.enabled is False
        assert "tool-capable" in avail.reason.lower()


async def test_unavailable_when_active_provider_has_no_model(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(
            session, admin_user.organization_id, admin_user.id, provider="anthropic", model=None
        )
        avail = await AssistantAvailabilityService.get_availability(session, admin_user.organization_id)
        assert avail.enabled is False


async def test_available_for_tool_capable_provider(db_engine, admin_user):
    async with _factory(db_engine)() as session:
        await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="anthropic")
        avail = await AssistantAvailabilityService.get_availability(session, admin_user.organization_id)
        assert avail.enabled is True
        assert avail.reason is None


# ---- Endpoint ----


async def test_availability_endpoint_reports_enabled(client, session, admin_user, admin_token):
    await _configure_active_provider(session, admin_user.organization_id, admin_user.id, provider="anthropic")
    resp = await client.get(
        "/api/assistant/availability",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["reason"] is None


async def test_availability_endpoint_requires_assistant_use(client, viewer_token):
    # viewer lacks assistant:use, so it cannot even query availability.
    resp = await client.get(
        "/api/assistant/availability",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403
