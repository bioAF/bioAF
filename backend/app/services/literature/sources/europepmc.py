"""Europe PMC adapter."""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.services.literature.sources import PaperRecord, RateLimit

logger = logging.getLogger("bioaf.literature.europepmc")

name = "europepmc"

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TIMEOUT = 30.0


def get_rate_limit(has_api_key: bool) -> RateLimit:
    return RateLimit(requests_per_second=10.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    params = {"query": query, "format": "json", "resultType": "core", "pageSize": str(min(max_results, 100))}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.get(f"{_BASE}/search", params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Europe PMC search failed: %s", e)
            return []
    return [_parse_result(item) for item in data.get("resultList", {}).get("result", [])]


async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None:
    results = await search(f'DOI:"{doi}"', 1, api_key)
    return results[0] if results else None


def _parse_result(item: dict) -> PaperRecord:
    title = (item.get("title") or "").strip()
    doi = (item.get("doi") or "").strip().lower() or None
    pmid = item.get("pmid") or None
    abstract = item.get("abstractText") or None
    journal = (item.get("journalTitle") or item.get("journalInfo", {}).get("journal", {}).get("title")) or None
    pub_date_str = item.get("firstPublicationDate") or item.get("electronicPublicationDate")
    pub_date = None
    if pub_date_str:
        try:
            pub_date = date.fromisoformat(pub_date_str)
        except ValueError:
            try:
                pub_date = date(int(pub_date_str[:4]), 1, 1)
            except (ValueError, IndexError):
                pub_date = None

    authors: list[dict] = []
    for au in item.get("authorList", {}).get("author", []) if isinstance(item.get("authorList"), dict) else []:
        family = au.get("lastName") or ""
        given = au.get("firstName") or au.get("initials") or ""
        if family or given:
            authors.append({"family": family, "given": given})

    has_full_text = item.get("inEPMC") == "Y" or item.get("isOpenAccess") == "Y"

    return PaperRecord(
        source=name,
        title=title,
        authors=authors,
        doi=doi,
        pmid=pmid,
        journal=journal,
        publication_date=pub_date,
        abstract=abstract,
        has_full_text=has_full_text,
    )
