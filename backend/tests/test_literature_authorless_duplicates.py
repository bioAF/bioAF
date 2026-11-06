"""Papers with no DOI and no authors deduplicate by title, matching the
database's fallback uniqueness rule (organization, normalized title, first and
last author keys, where empty keys still count). Seen live on blackbird: a
second author-less copy of a conference abstract crashed search ingest and left
the search running forever."""

from __future__ import annotations

import asyncio

import pytest

from app.services.literature import paper_service
from app.services.literature.sources import PaperRecord

TITLE = "8 Single Cell Transcriptomic Investigation of Renal Cell Carcinoma"


async def _authorless(session, admin_user, *, title=TITLE, source_search=True):
    paper = await paper_service.create_paper(
        session,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        title=title,
        authors=[],
        provenance="source_search" if source_search else "user_upload",
        source="europepmc",
        in_library=False,
    )
    await session.commit()
    return paper


@pytest.mark.asyncio
async def test_find_duplicate_matches_authorless_paper_by_title(session, admin_user):
    existing = await _authorless(session, admin_user)

    found = await paper_service.find_duplicate(
        session, org_id=admin_user.organization_id, doi=None, title=TITLE.upper() + "  ", authors=[]
    )

    assert found is not None and found.id == existing.id


@pytest.mark.asyncio
async def test_authorless_title_match_ignores_papers_with_authors(session, admin_user):
    await paper_service.create_paper(
        session,
        org_id=admin_user.organization_id,
        user_id=admin_user.id,
        title=TITLE,
        authors=[{"family": "Chen", "given": "S"}],
        provenance="source_search",
        source="pubmed",
        in_library=False,
    )
    await session.commit()

    found = await paper_service.find_duplicate(
        session, org_id=admin_user.organization_id, doi=None, title=TITLE, authors=[]
    )

    assert found is None


@pytest.mark.asyncio
async def test_second_authorless_copy_is_reported_as_duplicate_not_integrity_error(session, admin_user):
    existing = await _authorless(session, admin_user)

    with pytest.raises(paper_service.DuplicatePaper) as exc:
        await paper_service.create_paper(
            session,
            org_id=admin_user.organization_id,
            user_id=admin_user.id,
            title=TITLE,
            authors=None,
            provenance="source_search",
            source="semanticscholar",
            in_library=False,
        )

    assert exc.value.existing_paper_id == existing.id


@pytest.mark.asyncio
async def test_interactive_search_reuses_existing_authorless_paper_and_finishes(
    client, admin_token, admin_user, session, monkeypatch
):
    from app.services.bootstrap_literature import seed_literature_sources
    from app.services.literature.sources import biorxiv, europepmc, openalex, pubmed, semanticscholar

    await seed_literature_sources(session, admin_user.organization_id)
    existing = await _authorless(session, admin_user)

    async def authorless(query, max_results, api_key):
        return [PaperRecord(source="semanticscholar", title=TITLE, authors=[], doi=None)]

    async def empty(query, max_results, api_key):
        return []

    monkeypatch.setattr(semanticscholar, "search", authorless)
    for module in (pubmed, biorxiv, europepmc, openalex):
        monkeypatch.setattr(module, "search", empty)

    headers = {"Authorization": f"Bearer {admin_token}"}
    submit = await client.post(
        "/api/literature/searches",
        json={"query": "rcc", "sources": ["pubmed", "biorxiv", "europepmc", "semanticscholar"]},
        headers=headers,
    )
    assert submit.status_code == 201
    search_id = submit.json()["id"]

    final: dict = {}
    for _ in range(50):
        await asyncio.sleep(0.1)
        final = (await client.get(f"/api/literature/searches/{search_id}", headers=headers)).json()
        if final["status"] not in ("queued", "running"):
            break

    assert final["status"] == "complete"
    assert final["result_count"] == 1
    results = (await client.get(f"/api/literature/searches/{search_id}/results", headers=headers)).json()["items"]
    assert [r["id"] for r in results] == [existing.id]
