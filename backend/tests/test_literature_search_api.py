"""Sources configuration and ad-hoc search API tests.

The source adapters are monkey-patched to deterministic fakes so the test
suite stays offline. The actual HTTP shape of each adapter is exercised in
its own targeted tests (where applicable)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services.literature.sources import PaperRecord


@pytest.mark.asyncio
async def test_list_sources_returns_seeded_four(client, admin_token, admin_user, session):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()

    resp = await client.get(
        "/api/literature/sources", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    sources = {i["source"] for i in items}
    assert sources == {"pubmed", "biorxiv", "europepmc", "semanticscholar"}
    for item in items:
        assert item["enabled"] is True
        assert item["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_source_enables_and_sets_key(client, admin_token, admin_user, session):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()

    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.patch(
        "/api/literature/sources/pubmed",
        json={"enabled": True, "api_key": "secret-key", "rate_limit_override": 5},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_api_key"] is True
    assert body["rate_limit_override"] == 5


@pytest.mark.asyncio
async def test_viewer_cannot_configure_sources(client, viewer_token, admin_user, session):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()

    resp = await client.patch(
        "/api/literature/sources/pubmed",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_search_end_to_end_with_monkeypatched_sources(
    client, admin_token, admin_user, session, monkeypatch
):
    """Submit a search, let the background coroutine run, then poll for results.

    All four adapters are replaced with deterministic in-process fakes so this
    test does not hit external APIs."""
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()

    async def fake_search(query, max_results, api_key):
        return [
            PaperRecord(
                source="pubmed",
                title=f"Pubmed result for {query}",
                authors=[{"family": "Doe", "given": "Jane"}],
                doi=f"10.1234/pmid-{query}",
                journal="Nature",
                publication_date=date(2024, 5, 1),
                abstract="abstract text",
            )
        ]

    async def empty_search(query, max_results, api_key):
        return []

    from app.services.literature.sources import biorxiv, europepmc, pubmed, semanticscholar

    monkeypatch.setattr(pubmed, "search", fake_search)
    monkeypatch.setattr(biorxiv, "search", empty_search)
    monkeypatch.setattr(europepmc, "search", empty_search)
    monkeypatch.setattr(semanticscholar, "search", empty_search)

    headers = {"Authorization": f"Bearer {admin_token}"}
    submit = await client.post(
        "/api/literature/searches",
        json={"query": "tgf-beta", "sources": ["pubmed", "biorxiv", "europepmc", "semanticscholar"]},
        headers=headers,
    )
    assert submit.status_code == 201
    search_id = submit.json()["id"]

    # Poll until the search completes (or times out).
    for _ in range(40):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/literature/searches/{search_id}", headers=headers)
        if r.json()["status"] in {"complete", "partial", "failed"}:
            break
    final = r.json()
    assert final["status"] in {"complete", "partial"}
    assert final["result_count"] == 1

    # The result is now in the library.
    results = await client.get(
        f"/api/literature/searches/{search_id}/results", headers=headers
    )
    assert results.status_code == 200
    items = results.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Pubmed result for tgf-beta"
    assert items[0]["provenance"] == "source_search"


@pytest.mark.asyncio
async def test_search_failed_status_when_all_sources_fail(
    client, admin_token, admin_user, session, monkeypatch
):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()

    async def boom(query, max_results, api_key):
        raise RuntimeError("simulated outage")

    from app.services.literature.sources import biorxiv, europepmc, pubmed, semanticscholar

    monkeypatch.setattr(pubmed, "search", boom)
    monkeypatch.setattr(biorxiv, "search", boom)
    monkeypatch.setattr(europepmc, "search", boom)
    monkeypatch.setattr(semanticscholar, "search", boom)

    headers = {"Authorization": f"Bearer {admin_token}"}
    submit = await client.post(
        "/api/literature/searches",
        json={"query": "any query"},
        headers=headers,
    )
    search_id = submit.json()["id"]
    final_status = None
    for _ in range(40):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/literature/searches/{search_id}", headers=headers)
        final_status = r.json()
        if final_status["status"] in {"complete", "partial", "failed"}:
            break
    assert final_status is not None
    assert final_status["status"] == "failed", final_status
    assert final_status["result_count"] == 0
