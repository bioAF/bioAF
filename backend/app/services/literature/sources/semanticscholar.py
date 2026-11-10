"""Semantic Scholar adapter."""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.services.literature.sources import PaperRecord, RateLimit, author_from_full_name, sanitize_source_text

logger = logging.getLogger("bioaf.literature.semanticscholar")

name = "semanticscholar"

_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = 30.0
_FIELDS = "title,authors,abstract,year,venue,publicationDate,externalIds,openAccessPdf"


# The key identifies this deployment's quota with Semantic Scholar.
CREDENTIAL_SCOPED_QUOTA = True


def get_rate_limit(has_api_key: bool) -> RateLimit:
    # Checked 2026-11-09 (semanticscholar.org/product/api): unauthenticated
    # requests share one pool across all anonymous users and are throttled
    # under load, so this stays conservative; an individual key starts at
    # 1 request/second. A larger granted rate is set with rate_limit_override.
    return RateLimit(requests_per_second=0.5 if not has_api_key else 1.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    params = {"query": query, "limit": str(min(max_results, 100)), "fields": _FIELDS}
    headers = {"x-api-key": api_key} if api_key else {}
    # Errors propagate so callers classify an outage as a failed attempt.
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_BASE}/paper/search", params=params, headers=headers)
        r.raise_for_status()
        data = r.json()
    return [_parse_result(item) for item in data.get("data", [])]


async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None:
    headers = {"x-api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.get(f"{_BASE}/paper/DOI:{doi}", params={"fields": _FIELDS}, headers=headers)
            r.raise_for_status()
            return _parse_result(r.json())
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Semantic Scholar DOI fetch failed: %s", e)
            return None


def _parse_result(item: dict) -> PaperRecord:
    title = sanitize_source_text((item.get("title") or "").strip()) or ""
    abstract = sanitize_source_text(item.get("abstract") or None)
    journal = sanitize_source_text(item.get("venue") or None)
    pub_date = None
    pub_str = item.get("publicationDate")
    if pub_str:
        try:
            pub_date = date.fromisoformat(pub_str)
        except ValueError:
            pub_date = None
    elif item.get("year"):
        try:
            pub_date = date(int(item["year"]), 1, 1)
        except (TypeError, ValueError):
            pub_date = None
    ext = item.get("externalIds") or {}
    doi = (ext.get("DOI") or "").strip().lower() or None
    pmid = ext.get("PubMed") or None
    pdf = item.get("openAccessPdf") or {}
    pdf_url = pdf.get("url") if isinstance(pdf, dict) else None
    authors = [a for au in item.get("authors", []) or [] if (a := author_from_full_name(au.get("name")))]
    return PaperRecord(
        source=name,
        title=title,
        authors=authors,
        doi=doi,
        pmid=pmid,
        journal=journal,
        publication_date=pub_date,
        abstract=abstract,
        pdf_url=pdf_url,
        has_full_text=bool(pdf_url),
    )
