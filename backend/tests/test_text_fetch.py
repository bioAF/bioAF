"""The shared GET-text operation behind the GEO and ENA fetchers.

GEO supplementary listings/tables (ground truth) and GEO/ENA sample manifests
used two identical GET+gunzip fetchers. Both now use http_policy.fetch_text:
same redirects and decoding, plus the read policy's bounded retries. Injected
fetchers and each service's best-effort fallbacks are unchanged.
"""

from __future__ import annotations

import gzip

import httpx
import pytest
import respx

from app.services import http_policy
from app.services.literature import ground_truth_fetch_service
from app.services.literature.ground_truth_fetch_service import GroundTruthFetchService

pytestmark = pytest.mark.asyncio

URL = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1000/suppl/"


def _route(url, *steps):
    script = list(steps)

    def handler(request):
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return step

    return respx.get(url).mock(side_effect=handler)


async def test_text_is_returned_and_gz_payloads_are_decompressed():
    with respx.mock:
        _route(URL, httpx.Response(200, text="listing"))
        _route(URL + "t.tsv.gz", httpx.Response(200, content=gzip.compress(b"gene\tlogFC\n")))
        assert await http_policy.fetch_text(URL, label="t") == "listing"
        assert await http_policy.fetch_text(URL + "t.tsv.gz", label="t") == "gene\tlogFC\n"


async def test_redirects_are_still_followed():
    with respx.mock:
        _route(URL, httpx.Response(302, headers={"Location": URL + "moved/"}))
        _route(URL + "moved/", httpx.Response(200, text="here"))
        assert await http_policy.fetch_text(URL, label="t") == "here"


async def test_transient_failures_are_retried_then_recover():
    with respx.mock:
        route = _route(URL, httpx.ConnectError("x"), httpx.Response(503), httpx.Response(200, text="ok"))
        assert await http_policy.fetch_text(URL, label="t") == "ok"
    assert route.call_count == 3


async def test_a_final_failure_raises_for_the_caller_to_handle():
    with respx.mock:
        route = _route(URL, httpx.Response(404))
        with pytest.raises(httpx.HTTPStatusError):
            await http_policy.fetch_text(URL, label="t")
    assert route.call_count == 1


async def test_the_ground_truth_default_fetcher_uses_the_shared_operation():
    with respx.mock:
        route = _route(URL, httpx.Response(503), httpx.Response(200, text=""))
        candidates = await GroundTruthFetchService.fetch_geo_candidates("GSE1000")
    assert candidates == []
    assert route.call_count == 2


async def test_an_unreachable_listing_still_falls_back_to_nothing():
    with respx.mock:
        _route(URL, httpx.ConnectError("down"))
        assert await GroundTruthFetchService.fetch_geo_candidates("GSE1000") == []


async def test_an_injected_fetcher_still_bypasses_the_network():
    seen = []

    async def fetcher(url):
        seen.append(url)
        return ""

    assert await GroundTruthFetchService.fetch_geo_candidates("GSE1000", fetcher=fetcher) == []
    assert seen == [URL]
    assert ground_truth_fetch_service._http_fetch_text is not fetcher
