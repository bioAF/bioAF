"""Semantic Scholar adapter."""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.services.literature.sources import PaperRecord, RateLimit, sanitize_source_text

logger = logging.getLogger("bioaf.literature.semanticscholar")

name = "semanticscholar"

_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = 30.0
_FIELDS = "title,authors,abstract,year,venue,publicationDate,externalIds,openAccessPdf"


def get_rate_limit(has_api_key: bool) -> RateLimit:
    # Semantic Scholar publishes 100 / 5 min unauthenticated. v1 cap matches
    # the documented rate; an org-level api_key relaxes this via the
    # rate_limit_override in literature_sources_config.
    return RateLimit(requests_per_second=0.5 if not has_api_key else 5.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    params = {"query": query, "limit": str(min(max_results, 100)), "fields": _FIELDS}
    headers = {"x-api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.get(f"{_BASE}/paper/search", params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Semantic Scholar search failed: %s", e)
            return []
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
    authors: list[dict] = []
    for au in item.get("authors", []) or []:
        full = (au.get("name") or "").strip()
        if not full:
            continue
        if " " in full:
            tokens = full.split()
            authors.append({"given": " ".join(tokens[:-1]), "family": tokens[-1]})
        else:
            authors.append({"given": "", "family": full})
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
