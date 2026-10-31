"""OpenAlex Literature Source adapter. All HTTP is mocked; no live egress."""

from datetime import date

import httpx
import pytest
import respx

from app.services.literature.sources import get_adapter
from app.services.literature.sources.openalex import _BASE

_WORK = {
    "id": "https://openalex.org/W2741809807",
    "display_name": "TGF-<i>beta</i> signaling &amp; fibrosis",
    "doi": "https://doi.org/10.7717/PEERJ.4375",
    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/29456894"},
    "publication_date": "2018-02-13",
    "relevance_score": 812.5,
    "authorships": [
        {"author": {"display_name": "Heather Piwowar"}},
        {"author": {"display_name": "Priem"}},
    ],
    "primary_location": {"source": {"display_name": "PeerJ"}},
    "best_oa_location": {"pdf_url": "https://peerj.com/articles/4375.pdf"},
    "abstract_inverted_index": {"Open": [0], "access": [1, 3], "is": [2], "growing.": [4]},
}

_SPARSE_WORK = {
    "id": "https://openalex.org/W1",
    "display_name": "Untitled preprint",
    "doi": None,
    "ids": {},
    "publication_date": None,
    "authorships": [],
    "primary_location": None,
    "best_oa_location": None,
    "abstract_inverted_index": None,
}


@pytest.mark.asyncio
async def test_search_normalizes_an_openalex_work():
    with respx.mock:
        respx.get(f"{_BASE}/works").respond(json={"results": [_WORK]})
        [paper] = await get_adapter("openalex").search("tgf-beta", 5, None)

    assert paper.source == "openalex"
    assert paper.title == "TGF-beta signaling & fibrosis"
    assert paper.doi == "10.7717/peerj.4375"
    assert paper.pmid == "29456894"
    assert paper.journal == "PeerJ"
    assert paper.publication_date == date(2018, 2, 13)
    assert paper.abstract == "Open access is access growing."
    assert paper.authors == [{"given": "Heather", "family": "Piwowar"}, {"given": "", "family": "Priem"}]
    assert paper.source_score == 812.5
    assert paper.source_url == "https://openalex.org/W2741809807"
    assert paper.pdf_url == "https://peerj.com/articles/4375.pdf"
    assert paper.has_full_text is True


@pytest.mark.asyncio
async def test_search_tolerates_missing_metadata():
    with respx.mock:
        respx.get(f"{_BASE}/works").respond(json={"results": [_SPARSE_WORK]})
        [paper] = await get_adapter("openalex").search("x", 5, None)

    assert paper.title == "Untitled preprint"
    assert paper.doi is None and paper.pmid is None and paper.journal is None
    assert paper.abstract is None and paper.publication_date is None and paper.pdf_url is None
    assert paper.authors == []
    assert paper.has_full_text is False


@pytest.mark.asyncio
async def test_api_key_is_sent_as_a_header_not_in_the_url():
    with respx.mock:
        route = respx.get(f"{_BASE}/works").respond(json={"results": []})
        await get_adapter("openalex").search("x", 5, "secret-key")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert "secret-key" not in str(request.url)


@pytest.mark.asyncio
async def test_search_failure_is_raised_not_reported_as_zero_results():
    with respx.mock:
        respx.get(f"{_BASE}/works").respond(status_code=429)
        with pytest.raises(httpx.HTTPStatusError):
            await get_adapter("openalex").search("x", 5, None)


@pytest.mark.asyncio
async def test_fetch_by_doi_returns_the_matching_work_or_none():
    with respx.mock:
        route = respx.get(f"{_BASE}/works").respond(json={"results": [_WORK]})
        paper = await get_adapter("openalex").fetch_by_doi("10.7717/peerj.4375", None)
        assert paper is not None and paper.doi == "10.7717/peerj.4375"
        assert route.calls.last.request.url.params["filter"] == "doi:10.7717/peerj.4375"

        respx.get(f"{_BASE}/works").respond(json={"results": []})
        assert await get_adapter("openalex").fetch_by_doi("10.1/none", None) is None


@pytest.mark.asyncio
async def test_fetch_work_looks_up_by_doi_with_context_fields():
    from app.services.literature.sources import openalex

    with respx.mock:
        route = respx.get(f"{_BASE}/works").respond(json={"results": [{"id": "https://openalex.org/W1"}]})
        work = await openalex.fetch_work(doi="10.1/abc", pmid="123", api_key=None)

    assert work == {"id": "https://openalex.org/W1"}
    params = route.calls.last.request.url.params
    assert params["filter"] == "doi:10.1/abc"
    selected = set(params["select"].split(","))
    assert {"id", "is_retracted", "cited_by_count", "counts_by_year", "locations"} <= selected


@pytest.mark.asyncio
async def test_fetch_work_falls_back_to_pmid():
    from app.services.literature.sources import openalex

    with respx.mock:
        route = respx.get(f"{_BASE}/works").respond(json={"results": []})
        assert await openalex.fetch_work(doi=None, pmid="29456894", api_key=None) is None

    assert route.calls.last.request.url.params["filter"] == "pmid:29456894"


@pytest.mark.asyncio
async def test_fetch_work_without_an_identifier_makes_no_request():
    from app.services.literature.sources import openalex

    with respx.mock:
        route = respx.get(f"{_BASE}/works")
        assert await openalex.fetch_work(doi=None, pmid=None, api_key=None) is None

    assert not route.called


@pytest.mark.asyncio
async def test_search_keeps_the_small_field_selection():
    with respx.mock:
        route = respx.get(f"{_BASE}/works").respond(json={"results": []})
        await get_adapter("openalex").search("x", 5, None)

    assert "counts_by_year" not in route.calls.last.request.url.params["select"]
