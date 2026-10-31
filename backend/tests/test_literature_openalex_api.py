"""OpenAlex research context and open-access endpoints. OpenAlex is mocked; no live egress."""

from __future__ import annotations

import httpx
import pytest

from app.services.literature.sources import openalex

_WORK = {
    "id": "https://openalex.org/W99",
    "is_retracted": False,
    "cited_by_count": 7,
    "counts_by_year": [{"year": 2024, "cited_by_count": 7}],
    "locations": [],
}


async def _paper(client, token, **fields) -> int:
    r = await client.post(
        "/api/literature/papers",
        json={"title": "A paper", "authors": [{"given": "A", "family": "B"}], **fields},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
def lookups(monkeypatch):
    calls: list[dict] = []

    async def fake_fetch_work(*, doi, pmid, api_key):
        calls.append({"doi": doi, "pmid": pmid, "api_key": api_key})
        return _WORK

    monkeypatch.setattr(openalex, "fetch_work", fake_fetch_work)
    return calls


@pytest.mark.asyncio
async def test_context_for_a_matched_paper(client, admin_token, viewer_token, lookups):
    pid = await _paper(client, admin_token, doi="10.1/ctx")

    r = await client.get(f"/api/literature/papers/{pid}/openalex", headers={"Authorization": f"Bearer {viewer_token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["openalex_id"] == "W99"
    assert body["is_retracted"] is False
    assert body["cited_by_count"] == 7
    assert body["counts_by_year"] == [{"year": 2024, "cited_by_count": 7}]
    assert lookups[-1]["doi"] == "10.1/ctx"


@pytest.mark.asyncio
async def test_context_uses_the_orgs_openalex_key(client, admin_token, admin_user, session, lookups):
    from app.services.bootstrap_literature import seed_literature_sources

    await seed_literature_sources(session, admin_user.organization_id)
    await session.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.patch("/api/literature/sources/openalex", json={"api_key": "org-key"}, headers=headers)
    pid = await _paper(client, admin_token, doi="10.1/key")

    await client.get(f"/api/literature/papers/{pid}/openalex", headers=headers)

    assert lookups[-1]["api_key"] == "org-key"


@pytest.mark.asyncio
async def test_context_unavailable_without_identifier_or_match(client, admin_token, monkeypatch):
    async def no_match(*, doi, pmid, api_key):
        return None

    monkeypatch.setattr(openalex, "fetch_work", no_match)
    headers = {"Authorization": f"Bearer {admin_token}"}
    for pid in (await _paper(client, admin_token), await _paper(client, admin_token, doi="10.1/none")):
        r = await client.get(f"/api/literature/papers/{pid}/openalex", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["available"] is False


@pytest.mark.asyncio
async def test_context_outage_is_a_502_with_a_plain_message(client, admin_token, monkeypatch):
    async def outage(*, doi, pmid, api_key):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(openalex, "fetch_work", outage)
    pid = await _paper(client, admin_token, doi="10.1/down")

    r = await client.get(f"/api/literature/papers/{pid}/openalex", headers={"Authorization": f"Bearer {admin_token}"})

    assert r.status_code == 502
    assert r.json()["detail"] == "OpenAlex could not be reached right now."


@pytest.mark.asyncio
async def test_context_requires_authentication(client, admin_token, lookups):
    pid = await _paper(client, admin_token, doi="10.1/auth")

    r = await client.get(f"/api/literature/papers/{pid}/openalex")

    assert r.status_code == 401
    assert lookups == []


@pytest.mark.asyncio
async def test_context_never_reads_another_orgs_paper(client, admin_token, session, lookups):
    from app.models.organization import Organization
    from app.services.literature import paper_service

    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    paper = await paper_service.create_paper(
        session,
        org_id=other.id,
        user_id=None,
        title="Theirs",
        authors=[],
        doi="10.1/theirs",
        provenance="source_search",
    )
    await session.commit()

    r = await client.get(
        f"/api/literature/papers/{paper.id}/openalex", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert r.status_code == 404
    assert lookups == []
