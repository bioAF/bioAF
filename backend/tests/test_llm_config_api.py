"""Tests for the LLM provider config API (ADR-053).

Verifies:
- Admin can list, upsert, activate, deactivate, and delete.
- Non-admins (viewer) cannot reach any endpoint (403).
- Activating one provider with another already active flips the singleton.
- The response redacts the api_key but exposes the last-5 prefix.
- The model_lists section is present per provider, with used_fallback=true when
  no live network call is reachable in tests.
"""

from __future__ import annotations

import pytest



@pytest.fixture
def admin_auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def viewer_auth(viewer_token):
    return {"Authorization": f"Bearer {viewer_token}"}


@pytest.mark.asyncio
async def test_list_providers_returns_four_unconfigured_for_new_org(client, admin_auth):
    resp = await client.get("/api/integrations/llm/providers", headers=admin_auth)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    providers = {c["provider"] for c in data["configs"]}
    assert providers == {"openai", "anthropic", "google", "gemma"}
    assert all(c["configured"] is False for c in data["configs"])
    assert data["active_provider"] is None
    assert {ml["provider"] for ml in data["model_lists"]} == providers


@pytest.mark.asyncio
async def test_upsert_then_activate_flow(client, admin_auth):
    save = await client.post(
        "/api/integrations/llm/providers/openai",
        json={"api_key": "sk-XXXXX-end55", "model": "gpt-5"},
        headers=admin_auth,
    )
    assert save.status_code == 200, save.text
    assert save.json()["configured"] is True
    assert save.json()["api_key_prefix_last5"] == "end55"

    activate = await client.post("/api/integrations/llm/providers/openai/activate", headers=admin_auth)
    assert activate.status_code == 200, activate.text
    assert activate.json()["is_active"] is True

    listed = await client.get("/api/integrations/llm/providers", headers=admin_auth)
    assert listed.json()["active_provider"] == "openai"


@pytest.mark.asyncio
async def test_activate_flips_singleton(client, admin_auth):
    await client.post(
        "/api/integrations/llm/providers/openai",
        json={"api_key": "sk-openai-LAST5", "model": "gpt-5"},
        headers=admin_auth,
    )
    await client.post(
        "/api/integrations/llm/providers/anthropic",
        json={"api_key": "sk-anth-LAST5", "model": "claude-opus-4-7"},
        headers=admin_auth,
    )
    await client.post("/api/integrations/llm/providers/openai/activate", headers=admin_auth)
    await client.post("/api/integrations/llm/providers/anthropic/activate", headers=admin_auth)

    listed = (await client.get("/api/integrations/llm/providers", headers=admin_auth)).json()
    assert listed["active_provider"] == "anthropic"
    configs_by_provider = {c["provider"]: c for c in listed["configs"]}
    assert configs_by_provider["anthropic"]["is_active"] is True
    assert configs_by_provider["openai"]["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_all(client, admin_auth):
    await client.post(
        "/api/integrations/llm/providers/openai",
        json={"api_key": "sk-openai-LAST5", "model": "gpt-5"},
        headers=admin_auth,
    )
    await client.post("/api/integrations/llm/providers/openai/activate", headers=admin_auth)
    resp = await client.post("/api/integrations/llm/providers/deactivate", headers=admin_auth)
    assert resp.status_code == 204
    listed = (await client.get("/api/integrations/llm/providers", headers=admin_auth)).json()
    assert listed["active_provider"] is None


@pytest.mark.asyncio
async def test_viewer_forbidden(client, viewer_auth):
    resp = await client.get("/api/integrations/llm/providers", headers=viewer_auth)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unknown_provider_404(client, admin_auth):
    resp = await client.post(
        "/api/integrations/llm/providers/unknown",
        json={"api_key": "x", "model": "y"},
        headers=admin_auth,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_hosted_provider_requires_api_key(client, admin_auth):
    resp = await client.post(
        "/api/integrations/llm/providers/openai",
        json={"model": "gpt-5"},
        headers=admin_auth,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_gemma_save_without_key_succeeds(client, admin_auth):
    resp = await client.post(
        "/api/integrations/llm/providers/gemma",
        json={"model": "gemma-4-9b"},
        headers=admin_auth,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["api_key_prefix_last5"] is None


@pytest.mark.asyncio
async def test_delete_provider(client, admin_auth):
    await client.post(
        "/api/integrations/llm/providers/openai",
        json={"api_key": "sk-openai-LAST5", "model": "gpt-5"},
        headers=admin_auth,
    )
    resp = await client.delete("/api/integrations/llm/providers/openai", headers=admin_auth)
    assert resp.status_code == 204
    listed = (await client.get("/api/integrations/llm/providers", headers=admin_auth)).json()
    configs_by_provider = {c["provider"]: c for c in listed["configs"]}
    assert configs_by_provider["openai"]["configured"] is False


@pytest.mark.asyncio
async def test_model_list_falls_back_on_live_fetch_failure(client, admin_auth):
    """Without intercepting network the live fetch fails and we get the fallback list."""
    listed = (await client.get("/api/integrations/llm/providers", headers=admin_auth)).json()
    openai_list = next(ml for ml in listed["model_lists"] if ml["provider"] == "openai")
    assert openai_list["used_fallback"] is True
    assert "gpt-5" in openai_list["models"]
