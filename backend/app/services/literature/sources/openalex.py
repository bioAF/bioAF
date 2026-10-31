"""OpenAlex adapter.

Unlike the older adapters, HTTP failures raise so the search coordinator
records the source as failed instead of reporting zero results. The API key
travels in the Authorization header so it never appears in a logged URL.
"""

from __future__ import annotations

from datetime import date

import httpx

from app.services.literature.sources import PaperRecord, RateLimit, author_from_full_name, sanitize_source_text

name = "openalex"

_BASE = "https://api.openalex.org"
_TIMEOUT = 30.0
_SELECT = (
    "id,display_name,doi,ids,publication_date,relevance_score,authorships,"
    "primary_location,best_oa_location,abstract_inverted_index"
)
_CONTEXT_SELECT = "id,is_retracted,cited_by_count,counts_by_year,locations,best_oa_location"


def get_rate_limit(has_api_key: bool) -> RateLimit:
    # Published limits: 10 req/s without a key, 30 req/s with a free key.
    return RateLimit(requests_per_second=30.0 if has_api_key else 10.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    items = await _works({"search": query, "per_page": str(min(max_results, 200))}, api_key)
    return [_parse_result(item) for item in items]


async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None:
    items = await _works({"filter": f"doi:{doi}", "per_page": "1"}, api_key)
    return _parse_result(items[0]) if items else None


async def fetch_work(*, doi: str | None, pmid: str | None, api_key: str | None) -> dict | None:
    """The raw OpenAlex work for a paper, by DOI, else PMID, with the context fields."""
    if doi:
        id_filter = f"doi:{doi}"
    elif pmid:
        id_filter = f"pmid:{pmid}"
    else:
        return None
    items = await _works({"filter": id_filter, "per_page": "1"}, api_key, select=_CONTEXT_SELECT)
    return items[0] if items else None


async def _works(params: dict, api_key: str | None, select: str = _SELECT) -> list[dict]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_BASE}/works", params={**params, "select": select}, headers=headers)
        r.raise_for_status()
        return r.json().get("results", [])


def _strip_prefix(value: str | None, prefix: str) -> str | None:
    return (value or "").removeprefix(prefix).strip() or None


def _abstract(index: dict | None) -> str | None:
    """Rebuild text from OpenAlex's {word: [positions]} inverted index."""
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return sanitize_source_text(" ".join(word for _, word in words))


def _parse_result(item: dict) -> PaperRecord:
    pub_date = None
    if item.get("publication_date"):
        try:
            pub_date = date.fromisoformat(item["publication_date"])
        except ValueError:
            pub_date = None
    doi = _strip_prefix(item.get("doi"), "https://doi.org/")
    venue = (item.get("primary_location") or {}).get("source") or {}
    pdf_url = (item.get("best_oa_location") or {}).get("pdf_url")
    authors = [
        a
        for au in item.get("authorships") or []
        if (a := author_from_full_name((au.get("author") or {}).get("display_name")))
    ]
    return PaperRecord(
        source=name,
        title=sanitize_source_text(item.get("display_name")) or "",
        authors=authors,
        doi=doi.lower() if doi else None,
        pmid=_strip_prefix((item.get("ids") or {}).get("pmid"), "https://pubmed.ncbi.nlm.nih.gov/"),
        journal=sanitize_source_text(venue.get("display_name")),
        publication_date=pub_date,
        abstract=_abstract(item.get("abstract_inverted_index")),
        source_score=item.get("relevance_score"),
        source_url=item.get("id"),
        pdf_url=pdf_url,
        has_full_text=bool(pdf_url),
    )
