"""B1 full-text acquisition (lit_validation).

The reproduction extractor needs the article body (methods, accessions, data-availability), which the
lit-review sources do not return. FullTextFetchService pulls it from the Europe PMC REST backbone:
resolve the identifier to an open-access PMC record, fetch JATS fullTextXML, normalize to plain text.
These tests pin the resolve -> fetch -> normalize path and the "not openly reachable -> None" contract
(so the caller falls back to a pasted body). All HTTP is mocked; no live egress.
"""

import httpx
import pytest
import respx

from app.services.literature.fulltext_service import FullTextFetchService, _BASE

# JATS with a namespace, to prove the body is found regardless of namespace and tags are stripped.
_JATS = (
    '<?xml version="1.0"?>'
    '<article xmlns="http://jats.nlm.nih.gov">'
    "<front><article-meta><title-group><article-title>A study</article-title></title-group></article-meta></front>"
    "<body><sec><title>Methods</title>"
    "<p>We aligned reads with STAR. Raw data are available at GSE12345.</p></sec></body>"
    "</article>"
)

_OPEN_ACCESS_SEARCH = {
    "resultList": {"result": [{"pmcid": "PMC3258391", "inEPMC": "Y", "source": "PMC", "id": "3258391"}]}
}


@pytest.mark.asyncio
async def test_fetch_returns_normalized_body_text():
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json=_OPEN_ACCESS_SEARCH)
        respx.get(f"{_BASE}/PMC/PMC3258391/fullTextXML").respond(text=_JATS)

        result = await FullTextFetchService.fetch(doi="10.1/abc")

    assert result is not None
    assert result.source == "europepmc"
    assert result.external_id == "PMC3258391"
    assert "STAR" in result.text
    assert "GSE12345" in result.text
    assert "<p>" not in result.text  # tags stripped


@pytest.mark.asyncio
async def test_fetch_by_pmcid_skips_the_search_step():
    with respx.mock:
        search = respx.get(f"{_BASE}/search").respond(json={"resultList": {"result": []}})
        respx.get(f"{_BASE}/PMC/PMC3258391/fullTextXML").respond(text=_JATS)

        result = await FullTextFetchService.fetch(pmcid="PMC3258391")

    assert result is not None
    assert not search.called  # a known PMCID goes straight to full text


@pytest.mark.asyncio
async def test_fetch_returns_none_when_not_open_access():
    """An indexed-but-abstract-only paper (no PMC full text) resolves to None."""
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(
            json={"resultList": {"result": [{"pmid": "9", "source": "MED", "inEPMC": "N"}]}}
        )
        full = respx.get(f"{_BASE}/PMC/PMC0/fullTextXML").respond(text=_JATS)

        result = await FullTextFetchService.fetch(doi="10.1/closed")

    assert result is None
    assert not full.called  # never attempted a full-text pull


@pytest.mark.asyncio
async def test_fetch_returns_none_when_search_has_no_result():
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json={"resultList": {"result": []}})
        result = await FullTextFetchService.fetch(doi="10.1/missing")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_on_http_error():
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json=_OPEN_ACCESS_SEARCH)
        respx.get(f"{_BASE}/PMC/PMC3258391/fullTextXML").respond(status_code=404)
        result = await FullTextFetchService.fetch(doi="10.1/abc")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_without_any_identifier():
    # No network should be touched when there is nothing to resolve.
    result = await FullTextFetchService.fetch()
    assert result is None
