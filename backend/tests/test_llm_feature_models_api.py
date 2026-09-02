"""plan_6 steps 6 and 7 over HTTP: the per-feature model settings and the suitability warning."""

import pytest

from app.models.llm_provider_config import LlmProviderConfig

pytestmark = pytest.mark.asyncio


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _config(session, org_id, user_id, provider, model, active=False, key="k"):
    session.add(
        LlmProviderConfig(
            organization_id=org_id,
            provider=provider,
            api_key=key,
            model=model,
            is_active=active,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
    )
    await session.flush()
    await session.commit()


async def test_it_reports_the_model_each_feature_runs_on(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)

    r = await client.get("/api/integrations/llm/feature-models", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    by_feature = {f["feature"]: f for f in r.json()["features"]}

    assert set(by_feature) == {"literature_validation", "literature_review"}
    validation = by_feature["literature_validation"]
    assert validation["provider"] == "anthropic"
    assert validation["model"] == "claude-sonnet-4-6"
    # No override yet: this is the org's active provider, and the UI has to be able to say so.
    assert validation["overridden"] is False


async def test_it_carries_the_suitability_verdict_for_the_model_in_use(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)

    r = await client.get("/api/integrations/llm/feature-models", headers=_auth(admin_token))
    validation = next(f for f in r.json()["features"] if f["feature"] == "literature_validation")
    assert validation["suitability"]["verdict"] == "known_good"
    assert validation["suitability"]["warn"] is False


async def test_an_unlikely_model_warns_and_says_why(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "gemma", "gemma-4-9b", active=True)

    r = await client.get("/api/integrations/llm/feature-models", headers=_auth(admin_token))
    validation = next(f for f in r.json()["features"] if f["feature"] == "literature_validation")
    assert validation["suitability"]["verdict"] == "unlikely"
    assert validation["suitability"]["warn"] is True
    assert "context window" in validation["suitability"]["reason"]
    assert "provisional" in validation["suitability"]["note"].lower()


async def test_an_override_is_saved_and_reported(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)
    await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro", key="gk")

    put = await client.put(
        "/api/integrations/llm/feature-models/literature_validation",
        json={"provider": "google", "model": "gemini-2.5-flash"},
        headers=_auth(admin_token),
    )
    assert put.status_code == 200, put.text

    r = await client.get("/api/integrations/llm/feature-models", headers=_auth(admin_token))
    by_feature = {f["feature"]: f for f in r.json()["features"]}
    assert by_feature["literature_validation"]["model"] == "gemini-2.5-flash"
    assert by_feature["literature_validation"]["overridden"] is True
    # The other feature is untouched.
    assert by_feature["literature_review"]["model"] == "claude-sonnet-4-6"
    assert by_feature["literature_review"]["overridden"] is False


async def test_an_unlikely_override_still_saves(client, admin_token, session, admin_user):
    """The banner informs; it does not gate. The user proceeds at their discretion."""
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)
    await _config(session, admin_user.organization_id, admin_user.id, "gemma", "gemma-4-9b", key="gk")

    put = await client.put(
        "/api/integrations/llm/feature-models/literature_validation",
        json={"provider": "gemma", "model": "gemma-4-9b"},
        headers=_auth(admin_token),
    )
    assert put.status_code == 200, put.text
    assert put.json()["suitability"]["verdict"] == "unlikely"


async def test_an_override_on_an_unconfigured_provider_is_refused_by_name(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)

    put = await client.put(
        "/api/integrations/llm/feature-models/literature_validation",
        json={"provider": "google", "model": "gemini-2.5-pro"},
        headers=_auth(admin_token),
    )
    assert put.status_code == 400
    assert "google" in put.text


async def test_an_override_can_be_cleared(client, admin_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)
    await _config(session, admin_user.organization_id, admin_user.id, "google", "gemini-2.5-pro", key="gk")
    await client.put(
        "/api/integrations/llm/feature-models/literature_validation",
        json={"provider": "google", "model": "gemini-2.5-pro"},
        headers=_auth(admin_token),
    )

    delete = await client.delete(
        "/api/integrations/llm/feature-models/literature_validation", headers=_auth(admin_token)
    )
    assert delete.status_code == 204

    r = await client.get("/api/integrations/llm/feature-models", headers=_auth(admin_token))
    validation = next(f for f in r.json()["features"] if f["feature"] == "literature_validation")
    assert validation["overridden"] is False
    assert validation["provider"] == "anthropic"


async def test_a_viewer_cannot_set_one(client, viewer_token, session, admin_user):
    await _config(session, admin_user.organization_id, admin_user.id, "anthropic", "claude-sonnet-4-6", active=True)

    r = await client.put(
        "/api/integrations/llm/feature-models/literature_validation",
        json={"provider": "anthropic", "model": "claude-haiku-4-5"},
        headers=_auth(viewer_token),
    )
    assert r.status_code == 403
