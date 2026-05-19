"""Lit Review Run end-to-end tests with mocked LLM + source adapters.

The full flow uses the org's active LLM Provider to generate expansion queries,
fan out across sources, then score candidates. Both LLM calls and source
adapters are monkey-patched to deterministic in-process fakes so the test
suite stays offline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.services.literature.sources import PaperRecord


async def _make_experiment(session, admin_user):
    """Insert a minimal Experiment so the run has something to bind to."""
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            INSERT INTO experiments (name, status, organization_id, owner_user_id, project_id)
            VALUES ('Test Experiment', 'registered', :org, :uid, NULL)
            RETURNING id
            """
        ).bindparams(org=admin_user.organization_id, uid=admin_user.id)
    )
    eid = result.scalar_one()
    await session.commit()
    return eid


async def _seed_llm_provider(session, admin_user):
    from app.services import llm_provider_config_service

    await llm_provider_config_service.upsert(
        session,
        org_id=admin_user.organization_id,
        provider="anthropic",
        api_key="sk-test-fake-LAST5",
        model="claude-test",
        actor_user_id=admin_user.id,
    )
    await llm_provider_config_service.set_active(
        session,
        org_id=admin_user.organization_id,
        provider="anthropic",
        actor_user_id=admin_user.id,
    )
    await session.commit()


def _patch_sources(monkeypatch, records_per_query: list[PaperRecord]):
    async def fake_search(query, max_results, api_key):
        return list(records_per_query)

    async def empty_search(query, max_results, api_key):
        return []

    from app.services.literature.sources import (
        biorxiv,
        europepmc,
        pubmed,
        semanticscholar,
    )

    monkeypatch.setattr(pubmed, "search", fake_search)
    monkeypatch.setattr(biorxiv, "search", empty_search)
    monkeypatch.setattr(europepmc, "search", empty_search)
    monkeypatch.setattr(semanticscholar, "search", empty_search)


class _FakeLlmClient:
    """Stand-in for an `app.services.llm_provider_clients` module.

    Returns a configurable sequence of responses on each submit call so we can
    return query-list output on the first call and JSON scoring on the second."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    async def submit(self, prompt: str, payload: str, model: str, api_key: str | None,
                     attachments=None) -> str:
        if not self._responses:
            return ""
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_lit_review_run_creates_pending_recommendations(
    client, admin_token, admin_user, session, monkeypatch
):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await _seed_llm_provider(session, admin_user)
    eid = await _make_experiment(session, admin_user)

    # Source returns a candidate the LLM scoring will keep.
    candidate = PaperRecord(
        source="pubmed",
        title="TGF-beta in TNBC: a single-cell atlas",
        authors=[{"family": "Chen", "given": "Sarah"}],
        doi="10.9999/tgfb-tnbc",
        journal="Cell",
        publication_date=date(2024, 11, 1),
        abstract="We profile TGF-beta signalling subclones across 24 TNBC patients.",
    )
    _patch_sources(monkeypatch, [candidate])

    fake_responses = [
        "TGF-beta TNBC single-cell\nTGF-beta breast cancer subclones\nTGF-beta therapy windows",
        json.dumps([{"index": 0, "score": 0.88, "reasoning": "Directly relevant"}]),
    ]
    fake_client = _FakeLlmClient(fake_responses)
    from app.services import llm_provider_clients

    monkeypatch.setattr(llm_provider_clients, "get_client", lambda provider: fake_client)
    # Lit review run service imports get_client at module load; patch there too.
    from app.services.literature import lit_review_run_service

    monkeypatch.setattr(lit_review_run_service, "get_client", lambda provider: fake_client)

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        f"/api/literature/experiments/{eid}/lit-review-runs",
        json={"max_recommendations": 5, "score_threshold": 0.33},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    # Poll for completion.
    final = None
    for _ in range(80):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/literature/lit-review-runs/{run_id}", headers=headers)
        final = r.json()
        if final["status"] in {"complete", "partial", "failed"}:
            break
    assert final is not None
    assert final["status"] in {"complete", "partial"}, final
    assert final["recommendation_count"] == 1
    assert final["expansion_queries_json"] is not None
    assert len(final["expansion_queries_json"]) >= 1

    # Recommendation visible in the queue.
    recs = await client.get("/api/literature/recommendations", headers=headers)
    items = recs.json()["items"]
    assert len(items) == 1
    item = items[0]
    # Lit Review Runs auto-accept: the paper is added to the library and the
    # recommendation lands as 'accepted', associated with the source experiment.
    assert item["status"] == "accepted"
    assert item["relevance_bucket"] == "high"
    assert item["paper"]["title"] == candidate.title
    assert item["paper"]["in_library"] is True
    scopes = [(a["scope_type"], a["scope_id"]) for a in item["paper"]["associations"]]
    assert ("experiment", eid) in scopes


@pytest.mark.asyncio
async def test_accept_recommendation_associates_with_experiment(
    client, admin_token, admin_user, session, monkeypatch
):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await _seed_llm_provider(session, admin_user)
    eid = await _make_experiment(session, admin_user)

    candidate = PaperRecord(
        source="pubmed",
        title="Acceptance test paper",
        authors=[{"family": "Doe", "given": "Jane"}],
        doi="10.9999/accept",
        journal="Nature",
        publication_date=date(2024, 1, 1),
        abstract="abstract",
    )
    _patch_sources(monkeypatch, [candidate])
    fake_client = _FakeLlmClient(
        [
            "query one\nquery two",
            json.dumps([{"index": 0, "score": 0.75, "reasoning": "ok"}]),
        ]
    )
    from app.services.literature import lit_review_run_service

    monkeypatch.setattr(lit_review_run_service, "get_client", lambda provider: fake_client)

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        f"/api/literature/experiments/{eid}/lit-review-runs", json={}, headers=headers
    )
    run_id = resp.json()["id"]
    for _ in range(80):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/literature/lit-review-runs/{run_id}", headers=headers)
        if r.json()["status"] in {"complete", "partial", "failed"}:
            break

    recs = await client.get("/api/literature/recommendations", headers=headers)
    rec_id = recs.json()["items"][0]["id"]
    paper_id = recs.json()["items"][0]["paper"]["id"]

    accept = await client.post(
        f"/api/literature/recommendations/{rec_id}/accept", headers=headers
    )
    assert accept.status_code == 200
    assert accept.json()["status"] == "accepted"

    # Association now exists at experiment scope.
    paper = await client.get(f"/api/literature/papers/{paper_id}", headers=headers)
    scopes = [(a["scope_type"], a["scope_id"]) for a in paper.json()["associations"]]
    assert ("experiment", eid) in scopes


@pytest.mark.asyncio
async def test_dismiss_recommendation_dismisses_paper(
    client, admin_token, admin_user, session, monkeypatch
):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await _seed_llm_provider(session, admin_user)
    eid = await _make_experiment(session, admin_user)

    candidate = PaperRecord(
        source="pubmed",
        title="Dismiss test paper",
        authors=[{"family": "Doe", "given": "Jane"}],
        doi="10.9999/dis",
        journal="Nature",
        publication_date=date(2024, 1, 1),
        abstract="abstract",
    )
    _patch_sources(monkeypatch, [candidate])
    fake_client = _FakeLlmClient(
        ["q1\nq2", json.dumps([{"index": 0, "score": 0.7, "reasoning": "ok"}])]
    )
    from app.services.literature import lit_review_run_service

    monkeypatch.setattr(lit_review_run_service, "get_client", lambda provider: fake_client)

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        f"/api/literature/experiments/{eid}/lit-review-runs", json={}, headers=headers
    )
    run_id = resp.json()["id"]
    for _ in range(80):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/literature/lit-review-runs/{run_id}", headers=headers)
        if r.json()["status"] in {"complete", "partial", "failed"}:
            break

    recs = await client.get("/api/literature/recommendations", headers=headers)
    rec_id = recs.json()["items"][0]["id"]
    paper_id = recs.json()["items"][0]["paper"]["id"]

    dismiss = await client.post(
        f"/api/literature/recommendations/{rec_id}/dismiss", headers=headers
    )
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"

    paper = await client.get(f"/api/literature/papers/{paper_id}", headers=headers)
    assert paper.json()["dismissed"] is True


@pytest.mark.asyncio
async def test_no_active_llm_provider_fails_creation(
    client, admin_token, admin_user, session
):
    """Without an active provider, the run cannot be created."""
    eid = await _make_experiment(session, admin_user)
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.post(
        f"/api/literature/experiments/{eid}/lit-review-runs", json={}, headers=headers
    )
    assert resp.status_code == 409
