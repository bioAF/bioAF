"""B1 full-text acquisition (lit_validation).

The reproduction extractor needs the article body (methods, accessions, data-availability), which the
lit-review sources do not return. FullTextFetchService pulls it from the Europe PMC REST backbone:
resolve the identifier to an open-access PMC record, fetch JATS fullTextXML, normalize to plain text.
These tests pin the resolve -> fetch -> normalize path and the "not openly reachable -> None" contract
(so the caller falls back to a pasted body). All HTTP is mocked; no live egress.
"""

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

# Real full-text prose is riddled with statistical inequalities (P < 0.05, Q-value < 1E-10,
# enrichment scores > 1.5). Encoded as JATS text, these are `&lt;` / `&gt;` that ElementTree decodes
# back to literal `<` / `>`. The data-availability accession routinely sits between two such symbols.
_JATS_WITH_INEQUALITIES = (
    '<?xml version="1.0"?>'
    '<article xmlns="http://jats.nlm.nih.gov"><body><sec><title>Methods</title>'
    "<p>Differentially expressed genes had Q-value &lt;1E-10. Raw sequencing data are deposited at "
    "GEO under accession GSE52778. Enrichment scores &gt;1.5 were considered significant.</p>"
    "</sec></body></article>"
)


@pytest.mark.asyncio
async def test_fetch_returns_normalized_body_text():
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json=_OPEN_ACCESS_SEARCH)
        respx.get(f"{_BASE}/PMC3258391/fullTextXML").respond(text=_JATS)

        result = await FullTextFetchService.fetch(doi="10.1/abc")

    assert result is not None
    assert result.source == "europepmc"
    assert result.external_id == "PMC3258391"
    assert "STAR" in result.text
    assert "GSE12345" in result.text
    assert "<p>" not in result.text  # tags stripped


@pytest.mark.asyncio
async def test_fetch_requests_the_bare_pmcid_full_text_url():
    """Regression: the JATS full-text path is ``{BASE}/{PMCID}/fullTextXML`` (the PMCID already carries
    its ``PMC`` prefix, and there is NO extra source path segment). Europe PMC 404s on
    ``{BASE}/PMC/{PMCID}/fullTextXML``, so the earlier shape silently failed on every live fetch while
    the mocks (which encoded the wrong shape) stayed green."""
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json=_OPEN_ACCESS_SEARCH)
        full = respx.get(f"{_BASE}/PMC3258391/fullTextXML").respond(text=_JATS)

        result = await FullTextFetchService.fetch(doi="10.1/abc")

    assert result is not None
    assert full.called
    assert str(full.calls.last.request.url) == f"{_BASE}/PMC3258391/fullTextXML"


@pytest.mark.asyncio
async def test_fetch_preserves_prose_with_inequalities_and_accession():
    """Regression: full text must NOT be run through the abstract/title HTML sanitizer. Its
    ``<[^>]+>`` tag-stripper treats the span between a literal ``<`` (e.g. Q-value <1E-10) and the next
    ``>`` (e.g. scores >1.5) as a tag and deletes it, which in real papers swallows the very
    data-availability accession the reproduction extractor exists to read. ``itertext`` already yields
    tag-free plain text, so only whitespace needs normalizing."""
    with respx.mock:
        respx.get(f"{_BASE}/search").respond(json=_OPEN_ACCESS_SEARCH)
        respx.get(f"{_BASE}/PMC3258391/fullTextXML").respond(text=_JATS_WITH_INEQUALITIES)

        result = await FullTextFetchService.fetch(doi="10.1/abc")

    assert result is not None
    assert "GSE52778" in result.text  # the accession survives (would be eaten by the tag-stripper)
    assert "Q-value <1E-10" in result.text
    assert "scores >1.5" in result.text


@pytest.mark.asyncio
async def test_fetch_by_pmcid_skips_the_search_step():
    with respx.mock:
        search = respx.get(f"{_BASE}/search").respond(json={"resultList": {"result": []}})
        respx.get(f"{_BASE}/PMC3258391/fullTextXML").respond(text=_JATS)

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
        full = respx.get(f"{_BASE}/PMC0/fullTextXML").respond(text=_JATS)

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
        respx.get(f"{_BASE}/PMC3258391/fullTextXML").respond(status_code=404)
        result = await FullTextFetchService.fetch(doi="10.1/abc")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_returns_none_without_any_identifier():
    # No network should be touched when there is nothing to resolve.
    result = await FullTextFetchService.fetch()
    assert result is None
