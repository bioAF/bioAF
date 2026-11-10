"""Every Literature Source HTTP request is paced, retried, and counted.

Real adapters, respx upstreams, and a ManualClock registry: each upstream hit
records the virtual instant it was dispatched, so spacing is measured at the
send, not at a wrapper around ``search()``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.services import request_pacing
from app.services.literature import source_policy
from app.services.literature.fulltext_service import FullTextFetchService
from app.services.literature.source_policy import SourceExecution
from app.services.literature.sources import biorxiv, europepmc, openalex, pubmed, semanticscholar
from tests.support.request_clock import ManualClock

pytestmark = pytest.mark.asyncio

ESEARCH = f"{pubmed._BASE}/esearch.fcgi"
EFETCH = f"{pubmed._BASE}/efetch.fcgi"
EFETCH_XML = (
    "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article>"
    "<ArticleTitle>T</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
)


@pytest.fixture
def clock():
    clock = ManualClock()
    request_pacing.reset(clock)
    return clock


class Hits:
    def __init__(self, clock):
        self.clock = clock
        self.log: list[tuple[str, float]] = []

    def route(self, method, url, *responses, name=None, startswith=False):
        script = list(responses)

        def handler(request):
            self.log.append((name or url, self.clock.monotonic()))
            step = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(step, Exception):
                raise step
            return step

        kwargs = {"url__startswith": url} if startswith else {"url": url}
        respx.route(method=method, **kwargs).mock(side_effect=handler)

    def times(self, name=None):
        return [t for n, t in self.log if name is None or n == name]


async def _drive(clock, coro, step=0.05, limit=600):
    task = asyncio.create_task(coro)
    for _ in range(int(limit / step)):
        await clock.settle()
        if task.done():
            return await task
        await clock.advance(step)
    raise AssertionError("operation did not finish")


def _execution(source, rps, key=None):
    return SourceExecution(source=source, enabled=True, api_key=key, requests_per_second=rps)


async def test_both_pubmed_requests_are_paced_at_the_resolved_rate(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", ESEARCH, httpx.Response(200, json={"esearchresult": {"idlist": ["1"]}}), name="esearch")
        hits.route("GET", EFETCH, httpx.Response(200, text=EFETCH_XML), name="efetch")
        with source_policy.bound(_execution("pubmed", 2.0)):
            first = await _drive(clock, pubmed.search("q", 5, None))
            second = await _drive(clock, pubmed.search("q", 5, None))
    assert [r.title for r in first + second] == ["T", "T"]
    times = hits.times()
    assert len(times) == 4
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(times, times[1:]))


async def test_a_failed_efetch_is_retried_without_repeating_esearch(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", ESEARCH, httpx.Response(200, json={"esearchresult": {"idlist": ["1"]}}), name="esearch")
        hits.route("GET", EFETCH, httpx.Response(503), httpx.Response(200, text=EFETCH_XML), name="efetch")
        records = await _drive(clock, pubmed.search("q", 5, None))
    assert len(records) == 1
    assert len(hits.times("esearch")) == 1 and len(hits.times("efetch")) == 2


SEARCHES = {
    "europepmc": (europepmc, "GET", f"{europepmc._BASE}/search", {"resultList": {"result": []}}, False),
    "semanticscholar": (semanticscholar, "GET", f"{semanticscholar._BASE}/paper/search", {"data": []}, False),
    "biorxiv": (biorxiv, "GET", f"{biorxiv._BASE}/details/biorxiv/", {"collection": []}, True),
    "openalex": (openalex, "GET", f"{openalex._BASE}/works", {"results": []}, False),
}


@pytest.mark.parametrize("name", sorted(SEARCHES))
async def test_each_adapter_retries_a_transient_failure_then_recovers(clock, name):
    module, method, url, empty, prefix = SEARCHES[name]
    hits = Hits(clock)
    with respx.mock:
        hits.route(method, url, httpx.ConnectError("x"), httpx.Response(200, json=empty), startswith=prefix)
        assert await _drive(clock, module.search("q", 5, None)) == []
    assert len(hits.times()) == 2


@pytest.mark.parametrize("name", sorted(SEARCHES))
async def test_each_adapter_raises_after_retry_exhaustion(clock, name):
    module, method, url, _, prefix = SEARCHES[name]
    hits = Hits(clock)
    with respx.mock:
        hits.route(method, url, httpx.Response(503), startswith=prefix)
        with pytest.raises(httpx.HTTPStatusError):
            await _drive(clock, module.search("q", 5, None))
    assert len(hits.times()) == 3


async def test_a_429_cools_down_the_whole_bucket_for_other_operations(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route(
            "GET",
            f"{semanticscholar._BASE}/paper/search",
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"data": []}),
        )
        first = asyncio.create_task(semanticscholar.search("a", 5, None))
        await clock.settle()
        # A second operation in the same bucket starts during the cooldown.
        second = asyncio.create_task(semanticscholar.search("b", 5, None))
        await clock.advance(29)
        assert len(hits.times()) == 1
        await clock.advance(5)
        for _ in range(20):
            await clock.advance(2)
        await asyncio.gather(first, second)
    times = hits.times()
    assert len(times) == 3
    assert times[1] - times[0] >= 30 and times[2] - times[1] >= 2.0 - 1e-9


async def test_different_credentials_have_independent_buckets_and_equal_ones_share(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", f"{openalex._BASE}/works", httpx.Response(200, json={"results": []}))

        async def run(key):
            with source_policy.bound(_execution("openalex", 0.1, key)):
                await openalex.search("q", 5, key)

        # Two keys: both dispatch immediately.
        await asyncio.gather(run("key-a"), run("key-b"))
        assert hits.times() == [clock.now, clock.now]
        # The same key again waits a full interval behind the earlier send.
        task = asyncio.create_task(run("key-a"))
        await clock.settle()
        assert len(hits.times()) == 2
        await clock.advance(10)
        await task
    assert hits.times()[-1] - hits.times()[0] == pytest.approx(10)


async def test_providers_that_ignore_keys_share_one_service_bucket(clock):
    assert _execution("europepmc", 10, "a").bucket == _execution("europepmc", 10, "b").bucket
    assert _execution("biorxiv", 5, "a").bucket == _execution("biorxiv", 5, None).bucket
    assert _execution("pubmed", 10, "a").bucket != _execution("pubmed", 10, "b").bucket
    secret = "SYNTH-KEY-ZZZZ"
    fingerprint = _execution("pubmed", 10, secret).bucket[1]
    assert secret not in fingerprint and "ZZZZ" not in fingerprint
    assert _execution("pubmed", 10, secret).bucket == _execution("pubmed", 10, secret).bucket


async def test_europe_pmc_full_text_shares_the_search_bucket(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route(
            "GET",
            f"{europepmc._BASE}/search",
            httpx.Response(200, json={"resultList": {"result": [{"pmcid": "PMC1", "inEPMC": "Y"}]}}),
            name="search",
        )
        hits.route("GET", f"{europepmc._BASE}/PMC1/fullTextXML", httpx.Response(200, text="<a>body</a>"), name="xml")
        with source_policy.bound(_execution("europepmc", 1.0)):
            await europepmc.search("q", 5, None)
        result = await _drive(clock, FullTextFetchService.fetch(doi="10.1/x"))
    assert result is not None and result.text == "body"
    times = hits.times()
    # The full-text calls run at the shared service default (10/s) but wait out
    # the search dispatched at 1/s just before them.
    assert times[1] - times[0] >= 0.1 - 1e-9
    assert times[2] - times[1] >= 0.1 - 1e-9


async def test_openalex_work_lookup_is_paced(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", f"{openalex._BASE}/works", httpx.Response(200, json={"results": [{"id": "W1"}]}))
        with source_policy.bound(_execution("openalex", 1.0)):
            await openalex.fetch_work(doi="10.1/x", pmid=None, api_key=None)
            await _drive(clock, openalex.fetch_work(doi="10.1/y", pmid=None, api_key=None))
    times = hits.times()
    assert times[1] - times[0] >= 1.0 - 1e-9


@pytest.mark.parametrize(
    ("module", "url"),
    [
        (semanticscholar, f"{semanticscholar._BASE}/paper/DOI:10.1/x"),
        (biorxiv, f"{biorxiv._BASE}/details/biorxiv/10.1/x"),
        (europepmc, f"{europepmc._BASE}/search"),
    ],
)
async def test_best_effort_doi_lookups_still_return_none_after_retries(clock, module, url):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", url, httpx.Response(503))
        assert await _drive(clock, module.fetch_by_doi("10.1/x", None)) is None
    assert len(hits.times()) == 3


async def test_an_operation_budget_bounds_the_quota_wait(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", f"{europepmc._BASE}/search", httpx.Response(200, json={"resultList": {"result": []}}))
        request_pacing.registry().pacer(("europepmc", "service")).cool_down(500)
        with source_policy.bound(_execution("europepmc", 10.0), budget_seconds=180):
            with pytest.raises(TimeoutError):
                await europepmc.search("q", 5, None)
    assert hits.times() == []


async def test_cancelling_during_a_quota_wait_sends_nothing_later(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route("GET", f"{europepmc._BASE}/search", httpx.Response(200, json={"resultList": {"result": []}}))
        request_pacing.registry().pacer(("europepmc", "service")).cool_down(30)
        task = asyncio.create_task(europepmc.search("q", 5, None))
        await clock.settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await clock.advance(60)
    assert hits.times() == []


async def test_source_diagnostics_distinguish_wait_throttle_and_exhaustion_without_secrets(clock, caplog):
    caplog.set_level("DEBUG", logger="bioaf")
    hits = Hits(clock)
    key = "SYNTH-S2-KEY-0123456789"
    with respx.mock:
        hits.route(
            "GET",
            f"{semanticscholar._BASE}/paper/search",
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(503),
        )
        with source_policy.bound(_execution("semanticscholar", 0.5, key)):
            with pytest.raises(httpx.HTTPStatusError):
                await _drive(clock, semanticscholar.search("q", 5, key))
    text = caplog.text
    assert "source throttled: label=semanticscholar.search cooldown=3.00s" in text
    assert "source request paced: label=semanticscholar.search waited=" in text
    assert "http request ended unsuccessfully: label=semanticscholar.search" in text and "attempts=3" in text
    assert key not in text and "api.semanticscholar.org" not in text


async def test_quota_and_budget_failures_keep_distinct_categories(clock):
    from app.services.http_policy import BudgetExhausted
    from app.services.request_pacing import QuotaWaitExceeded

    with respx.mock:
        respx.get(f"{europepmc._BASE}/search").mock(
            return_value=httpx.Response(200, json={"resultList": {"result": []}})
        )
        request_pacing.registry().pacer(("europepmc", "service")).cool_down(500)
        with source_policy.bound(_execution("europepmc", 10.0), budget_seconds=10):
            with pytest.raises(QuotaWaitExceeded):
                await europepmc.search("q", 5, None)
        with source_policy.bound(_execution("europepmc", 10.0), budget_seconds=0):
            with pytest.raises(BudgetExhausted):
                await europepmc.search("q", 5, None)


async def test_an_overflowing_retry_hint_does_not_block_the_bucket(clock):
    hits = Hits(clock)
    with respx.mock:
        hits.route(
            "GET",
            f"{europepmc._BASE}/search",
            httpx.Response(429, headers={"Retry-After": "9" * 400}),
            httpx.Response(200, json={"resultList": {"result": []}}),
        )
        assert await _drive(clock, europepmc.search("a", 5, None)) == []
        assert await _drive(clock, europepmc.search("b", 5, None)) == []
    times = hits.times()
    assert len(times) == 3
    assert times[-1] - times[0] < 10


@pytest.mark.parametrize(
    "call",
    [
        lambda: FullTextFetchService.fetch(pmcid="PMC1"),
        lambda: europepmc.fetch_by_doi("10.1/x", None),
        lambda: biorxiv.fetch_by_doi("10.1/x", None),
        lambda: semanticscholar.fetch_by_doi("10.1/x", None),
    ],
    ids=["full_text", "europepmc_doi", "biorxiv_doi", "semanticscholar_doi"],
)
async def test_best_effort_lookups_return_none_when_quota_runs_out(clock, call):
    for source in ("europepmc", "biorxiv", "semanticscholar"):
        request_pacing.registry().pacer((source, "service")).cool_down(500)
    with respx.mock(assert_all_called=False) as router:
        router.route().respond(200, json={})
        assert await call() is None
        assert router.calls.call_count == 0
