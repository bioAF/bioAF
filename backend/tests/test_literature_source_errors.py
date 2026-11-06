"""Literature Source adapters report search failures instead of hiding them.

A source that is down, rate limited, or returns an unreadable body must raise
from search(), so the AI Literature Review and interactive search classify it
as a failed attempt rather than a successful search that found nothing. All
HTTP is mocked; no live egress.
"""

import httpx
import pytest
import respx

from app.services.literature.sources import biorxiv, europepmc, get_adapter, semanticscholar

SEARCH_ROUTES = {
    "europepmc": f"{europepmc._BASE}/search",
    "semanticscholar": f"{semanticscholar._BASE}/paper/search",
    "biorxiv": f"{biorxiv._BASE}/details/biorxiv/",
}


def _route(name: str):
    url = SEARCH_ROUTES[name]
    return respx.get(url__startswith=url) if name == "biorxiv" else respx.get(url)


@pytest.mark.parametrize("name", sorted(SEARCH_ROUTES))
@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.asyncio
async def test_search_raises_on_http_error_status(name, status):
    with respx.mock:
        _route(name).respond(status_code=status)
        with pytest.raises(httpx.HTTPStatusError):
            await get_adapter(name).search("tgf-beta", 5, None)


@pytest.mark.parametrize("name", sorted(SEARCH_ROUTES))
@pytest.mark.asyncio
async def test_search_raises_on_transport_error(name):
    with respx.mock:
        _route(name).mock(side_effect=httpx.ConnectError("unreachable"))
        with pytest.raises(httpx.ConnectError):
            await get_adapter(name).search("tgf-beta", 5, None)


@pytest.mark.parametrize("name", sorted(SEARCH_ROUTES))
@pytest.mark.asyncio
async def test_search_raises_on_unreadable_body(name):
    with respx.mock:
        _route(name).respond(status_code=200, text="<html>maintenance</html>")
        with pytest.raises(ValueError):
            await get_adapter(name).search("tgf-beta", 5, None)


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("europepmc", {"resultList": {"result": []}}),
        ("semanticscholar", {"data": []}),
        ("biorxiv", {"collection": []}),
    ],
)
@pytest.mark.asyncio
async def test_search_with_no_matches_still_returns_empty(name, body):
    with respx.mock:
        _route(name).respond(json=body)
        assert await get_adapter(name).search("tgf-beta", 5, None) == []


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("europepmc", f"{europepmc._BASE}/search"),
        ("semanticscholar", f"{semanticscholar._BASE}/paper/DOI:10.1/x"),
        ("biorxiv", f"{biorxiv._BASE}/details/biorxiv/10.1/x"),
    ],
)
@pytest.mark.asyncio
async def test_fetch_by_doi_still_returns_none_on_failure(name, url):
    with respx.mock:
        respx.get(url).respond(status_code=429)
        assert await get_adapter(name).fetch_by_doi("10.1/x", None) is None
