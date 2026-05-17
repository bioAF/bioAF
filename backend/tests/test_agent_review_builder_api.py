"""Tests for the new builder/preview/saved-prompt API endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def admin_auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest_asyncio.fixture
async def viewer_auth(viewer_token):
    return {"Authorization": f"Bearer {viewer_token}"}


@pytest.mark.asyncio
async def test_section_catalog_returned(client, admin_auth):
    resp = await client.get("/api/agent_reviews/section_catalog", headers=admin_auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    section_ids = [s["id"] for s in body["sections"]]
    assert {"qc", "metadata", "bio", "xsample", "interp"}.issubset(section_ids)
    # xsample is experiment_only.
    xsample = next(s for s in body["sections"] if s["id"] == "xsample")
    assert xsample["experiment_only"] is True
    # Defaults: pipeline-run defaults don't include xsample.* ids.
    assert not any(d.startswith("xsample.") for d in body["pipeline_run_defaults"])
    # Experiment defaults do include xsample.*.
    assert any(d.startswith("xsample.") for d in body["experiment_defaults"])


@pytest.mark.asyncio
async def test_section_catalog_requires_use_permission(client, viewer_auth):
    resp = await client.get("/api/agent_reviews/section_catalog", headers=viewer_auth)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_assemble_prompt_preview(client, admin_auth):
    resp = await client.post(
        "/api/agent_reviews/assemble_prompt",
        json={
            "entity_type": "pipeline_run",
            "selected_sub_item_ids": ["qc.metric_review", "interp.confidence"],
        },
        headers=admin_auth,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["body"]
    assert "## Quality control and technical assessment" in body
    assert "## Interpretation and Recommendations" in body
    assert "single pipeline run output" in body


@pytest.mark.asyncio
async def test_assemble_prompt_empty_returns_400(client, admin_auth):
    resp = await client.post(
        "/api/agent_reviews/assemble_prompt",
        json={"entity_type": "pipeline_run", "selected_sub_item_ids": []},
        headers=admin_auth,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_list_delete_saved_prompt(client, admin_auth):
    created = await client.post(
        "/api/agent_reviews/prompts",
        json={"name": "Std", "body": "body text"},
        headers=admin_auth,
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["id"]
    assert created.json()["name"] == "Std"
    assert "created_by_user_label" in created.json()

    listed = await client.get("/api/agent_reviews/prompts", headers=admin_auth)
    assert listed.status_code == 200
    assert any(p["id"] == prompt_id for p in listed.json()["items"])

    deleted = await client.delete(f"/api/agent_reviews/prompts/{prompt_id}", headers=admin_auth)
    assert deleted.status_code == 204

    listed2 = await client.get("/api/agent_reviews/prompts", headers=admin_auth)
    assert all(p["id"] != prompt_id for p in listed2.json()["items"])


@pytest.mark.asyncio
async def test_duplicate_saved_prompt_name_returns_409(client, admin_auth):
    await client.post(
        "/api/agent_reviews/prompts",
        json={"name": "dup", "body": "b"},
        headers=admin_auth,
    )
    second = await client.post(
        "/api/agent_reviews/prompts",
        json={"name": "dup", "body": "b2"},
        headers=admin_auth,
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_delete_nonexistent_saved_prompt_returns_404(client, admin_auth):
    resp = await client.delete("/api/agent_reviews/prompts/99999", headers=admin_auth)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_saved_prompts_endpoint_forbidden_for_viewer(client, viewer_auth):
    resp = await client.get("/api/agent_reviews/prompts", headers=viewer_auth)
    assert resp.status_code == 403
