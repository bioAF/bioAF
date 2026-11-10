"""Source settings govern real outbound traffic at every production entry point.

Interactive search, AI Literature Review collection, connection tests, and the
OpenAlex paper routes each resolve the organization's configuration once and
send through the shared source buckets. Real adapters answer respx upstreams;
a ManualClock records each dispatch instant, so a persisted override is shown
to change measured dispatch spacing, not only a stored number.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from sqlalchemy import select

import app.database as _database
from app.models.literature import LiteratureSearch
from app.services import request_pacing
from app.services.bootstrap_literature import seed_literature_sources
from app.services.literature import search_service, sources_config_service
from app.services.literature.sources import openalex, pubmed
from tests.support.request_clock import ManualClock, drive
from tests.test_literature_lit_review_outcomes import REAL_SEARCH, scores

pytestmark = pytest.mark.asyncio

ESEARCH = f"{pubmed._BASE}/esearch.fcgi"
EFETCH = f"{pubmed._BASE}/efetch.fcgi"
EFETCH_XML = (
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
    "<ArticleTitle>Paced paper</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)


@pytest.fixture
def clock():
    clock = ManualClock()
    request_pacing.reset(clock)
    return clock


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class Upstream:
    def __init__(self, clock):
        self.clock = clock
        self.hits: list[tuple[str, float, httpx.Request]] = []

    def route(self, url, response, name, startswith=False):
        def handler(request):
            self.hits.append((name, self.clock.monotonic(), request))
            return response

        kwargs = {"url__startswith": url} if startswith else {"url": url}
        respx.get(**kwargs).mock(side_effect=handler)

    def pubmed(self):
        self.route(ESEARCH, httpx.Response(200, json={"esearchresult": {"idlist": ["1"]}}), "esearch")
        self.route(EFETCH, httpx.Response(200, text=EFETCH_XML), "efetch")

    def times(self, *names):
        return [t for n, t, _ in self.hits if not names or n in names]

    def gaps(self, *names):
        times = self.times(*names)
        return [round(b - a, 6) for a, b in zip(times, times[1:])]


async def _configure(session, user, source="pubmed", **changes):
    await seed_literature_sources(session, user.organization_id)
    if changes:
        await sources_config_service.update(
            session, org_id=user.organization_id, source=source, user_id=user.id, **changes
        )
    await session.commit()


async def _search(session, user, sources):
    search = await search_service.create_search(
        session, org_id=user.organization_id, user_id=user.id, query="q", sources=sources
    )
    await session.commit()
    return search.id


async def _load_search(search_id):
    async with _database.async_session_factory() as s:
        return (await s.execute(select(LiteratureSearch).where(LiteratureSearch.id == search_id))).scalar_one()


# --- Interactive search -----------------------------------------------------------


@pytest.mark.parametrize(("override", "gap"), [(None, 1 / 3), (1, 1.0)])
async def test_a_persisted_override_changes_interactive_dispatch_spacing(clock, session, admin_user, override, gap):
    await _configure(session, admin_user, rate_limit_override=override)
    search_id = await _search(session, admin_user, ["pubmed"])
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        await drive(clock, search_service._run_search(search_id, admin_user.id, 5), step=0.01)
    assert (await _load_search(search_id)).status == "complete"
    # esearch then efetch: two dispatches in one source operation, spaced by the rate.
    assert upstream.gaps() == [pytest.approx(gap, abs=0.011)]


async def test_interactive_search_sends_the_configured_key(clock, session, admin_user):
    await _configure(session, admin_user, api_key="SYNTH-PUBMED-KEY")
    search_id = await _search(session, admin_user, ["pubmed"])
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        await drive(clock, search_service._run_search(search_id, admin_user.id, 5))
    assert all(r.url.params.get("api_key") == "SYNTH-PUBMED-KEY" for _, _, r in upstream.hits)


async def test_an_ordinary_search_runs_only_enabled_sources(client, admin_token, admin_user, session, monkeypatch):
    await _configure(session, admin_user, source="biorxiv", enabled=False)
    monkeypatch.setattr(search_service, "schedule_search_run", _no_schedule)
    resp = await client.post("/api/literature/searches", json={"query": "tgf"}, headers=_auth(admin_token))
    assert resp.status_code == 201
    assert "biorxiv" not in resp.json()["sources"]
    assert len(resp.json()["sources"]) == 4


async def test_explicitly_requesting_a_disabled_source_is_refused(client, admin_token, admin_user, session):
    await _configure(session, admin_user, source="openalex", enabled=False)
    resp = await client.post(
        "/api/literature/searches", json={"query": "tgf", "sources": ["openalex"]}, headers=_auth(admin_token)
    )
    assert resp.status_code == 409
    assert "openalex" in resp.json()["detail"].lower() or "OpenAlex" in resp.json()["detail"]


async def test_a_search_with_no_enabled_sources_is_refused(client, admin_token, admin_user, session):
    await seed_literature_sources(session, admin_user.organization_id)
    for source in ("pubmed", "biorxiv", "europepmc", "semanticscholar", "openalex"):
        await sources_config_service.update(
            session, org_id=admin_user.organization_id, source=source, user_id=admin_user.id, enabled=False
        )
    await session.commit()
    resp = await client.post("/api/literature/searches", json={"query": "tgf"}, headers=_auth(admin_token))
    assert resp.status_code == 409


async def test_a_source_disabled_after_submission_is_not_contacted(clock, session, admin_user):
    await _configure(session, admin_user)
    search_id = await _search(session, admin_user, ["pubmed", "openalex"])
    await sources_config_service.update(
        session, org_id=admin_user.organization_id, source="pubmed", user_id=admin_user.id, enabled=False
    )
    await session.commit()
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        upstream.route(f"{openalex._BASE}/works", httpx.Response(200, json={"results": []}), "openalex")
        await drive(clock, search_service._run_search(search_id, admin_user.id, 5))
    search = await _load_search(search_id)
    assert search.per_source_status["pubmed"] == "failed: SourceDisabled"
    assert search.per_source_status["openalex"] == "complete"
    assert search.status == "partial"
    assert upstream.times("esearch", "efetch") == []


async def _no_schedule(**kwargs):
    return None


# --- Connection tests share the source quota -----------------------------------------


async def test_a_connection_test_waits_behind_a_search_in_the_same_bucket(
    clock, client, admin_token, admin_user, session
):
    await _configure(session, admin_user, rate_limit_override=1)
    search_id = await _search(session, admin_user, ["pubmed"])
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        search = asyncio.ensure_future(search_service._run_search(search_id, admin_user.id, 5))
        probe = client.post("/api/literature/sources/pubmed/test", headers=_auth(admin_token))
        resp = await drive(clock, probe, step=0.01)
        await drive(clock, search, step=0.01)
    assert resp.json()["success"] is True
    times = sorted(upstream.times())
    assert len(times) == 4
    assert all(b - a >= 1.0 - 1e-6 for a, b in zip(times, times[1:]))


async def test_a_disabled_source_can_still_be_tested_explicitly(clock, client, admin_token, admin_user, session):
    await _configure(session, admin_user, enabled=False)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        resp = await drive(clock, client.post("/api/literature/sources/pubmed/test", headers=_auth(admin_token)))
    assert resp.json()["success"] is True
    assert upstream.times("esearch")


# --- AI Literature Review ---------------------------------------------------------------


async def test_review_collection_uses_the_resolved_rate_and_key(clock, session, admin_user, monkeypatch):
    from tests.test_literature_lit_review_outcomes import Harness

    h = Harness(session, admin_user, monkeypatch)
    await h.setup(sources=("pubmed",))
    monkeypatch.setattr(pubmed, "search", REAL_SEARCH["pubmed"])
    await sources_config_service.update(
        session,
        org_id=admin_user.organization_id,
        source="pubmed",
        user_id=admin_user.id,
        rate_limit_override=1,
        api_key="SYNTH-REVIEW-KEY",
    )
    await session.commit()
    h.script_llm("q1\nq2", scores((0, 0.9)))
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        run_id = await h.create_run()
        await drive(clock, h.execute(run_id), step=0.01)
    run = await h.load(run_id)
    assert run.status == "complete", run.error_message
    assert len(upstream.times()) == 4
    assert all(g >= 1.0 - 1e-6 for g in upstream.gaps())
    assert all(r.url.params.get("api_key") == "SYNTH-REVIEW-KEY" for _, _, r in upstream.hits)


# --- OpenAlex paper routes -----------------------------------------------------------------


async def _paper(session, user):
    from app.models.literature import PROVENANCE_SOURCE_SEARCH
    from app.services.literature import paper_service

    paper = await paper_service.create_paper(
        session,
        org_id=user.organization_id,
        user_id=user.id,
        title="A paper",
        authors=[{"family": "Doe", "given": "J"}],
        doi="10.1/abc",
        provenance=PROVENANCE_SOURCE_SEARCH,
        source="openalex",
        in_library=True,
    )
    await session.commit()
    return paper.id


async def test_openalex_context_lookups_are_paced_by_the_override(clock, client, admin_token, admin_user, session):
    await _configure(session, admin_user, source="openalex", rate_limit_override=1)
    paper_id = await _paper(session, admin_user)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.route(f"{openalex._BASE}/works", httpx.Response(200, json={"results": [{"id": "W1"}]}), "works")
        for _ in range(2):
            resp = await drive(
                clock, client.get(f"/api/literature/papers/{paper_id}/openalex", headers=_auth(admin_token)), step=0.01
            )
            assert resp.status_code == 200
    assert upstream.gaps() == [pytest.approx(1.0, abs=0.011)]


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/literature/papers/{id}/openalex"), ("POST", "/api/literature/papers/{id}/fetch-open-access")],
)
async def test_openalex_routes_do_not_contact_a_disabled_source(
    clock, client, admin_token, admin_user, session, method, path, monkeypatch
):
    from app.api.literature import papers as papers_api

    async def bucket(_session):
        return "bucket"

    monkeypatch.setattr(papers_api.storage, "get_literature_bucket", bucket)
    await _configure(session, admin_user, source="openalex", enabled=False)
    paper_id = await _paper(session, admin_user)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.route(f"{openalex._BASE}/works", httpx.Response(200, json={"results": []}), "works")
        resp = await client.request(method, path.format(id=paper_id), headers=_auth(admin_token))
    assert resp.status_code == 409
    assert "OpenAlex" in resp.json()["detail"]
    assert upstream.hits == []


# --- Cancellation while waiting for quota ---------------------------------------------------


async def test_a_search_cancelled_during_a_quota_wait_sends_nothing_later(clock, session, admin_user):
    await _configure(session, admin_user)
    search_id = await _search(session, admin_user, ["pubmed"])
    request_pacing.registry().pacer(("pubmed", "service")).cool_down(120)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        task = asyncio.ensure_future(search_service._run_search(search_id, admin_user.id, 5))
        for _ in range(20):
            await clock.advance(0.5)
        assert clock.sleeping >= 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await clock.advance(300)
    assert upstream.hits == []
    assert clock.sleeping == 0


async def test_a_review_cancelled_during_a_quota_wait_records_cancellation(clock, session, admin_user, monkeypatch):
    from tests.test_literature_lit_review_outcomes import Harness

    h = Harness(session, admin_user, monkeypatch)
    await h.setup(sources=("pubmed",))
    monkeypatch.setattr(pubmed, "search", REAL_SEARCH["pubmed"])
    h.script_llm("q1", scores((0, 0.9)))
    request_pacing.registry().pacer(("pubmed", "service")).cool_down(120)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        run_id = await h.create_run()
        task = asyncio.ensure_future(h.execute(run_id))
        for _ in range(40):
            await clock.advance(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await clock.advance(300)
    run = await h.load(run_id)
    assert run.status == "failed" and run.error_message == "cancelled"
    assert upstream.hits == []


async def test_a_quota_wait_past_the_review_budget_is_a_failed_attempt_not_a_hang(
    clock, session, admin_user, monkeypatch
):
    from tests.test_literature_lit_review_outcomes import Harness

    h = Harness(session, admin_user, monkeypatch)
    await h.setup(sources=("pubmed",))
    monkeypatch.setattr(pubmed, "search", REAL_SEARCH["pubmed"])
    h.script_llm("q1", scores((0, 0.9)))
    request_pacing.registry().pacer(("pubmed", "service")).cool_down(10_000)
    upstream = Upstream(clock)
    with respx.mock:
        upstream.pubmed()
        run_id = await h.create_run()
        await drive(clock, h.execute(run_id), step=1.0)
    run = await h.load(run_id)
    assert run.status == "failed"
    assert "pubmed 1/1 attempts failed" in (run.error_message or "")
    audit = [a for a in await h.audits(run_id) if a.action in ("fail", "failed", "complete")]
    assert audit, "the run's terminal audit is recorded"
    assert upstream.hits == []
