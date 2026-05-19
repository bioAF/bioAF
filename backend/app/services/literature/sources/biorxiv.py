"""bioRxiv (and medRxiv) adapter.

bioRxiv's public API is keyword-thin; for richer search, we fall back to
the EuropePMC adapter (which indexes bioRxiv preprints). v1 supports DOI
lookup directly and best-effort keyword search via the date-range pub
endpoint.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from app.services.literature.sources import PaperRecord, RateLimit

logger = logging.getLogger("bioaf.literature.biorxiv")

name = "biorxiv"

_BASE = "https://api.biorxiv.org"
_TIMEOUT = 30.0


def get_rate_limit(has_api_key: bool) -> RateLimit:
    return RateLimit(requests_per_second=5.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    """Best-effort keyword search across the last 30 days of preprints. For
    deeper search, Europe PMC is the better entry point."""
    end = date.today()
    start = end - timedelta(days=30)
    interval = f"{start.isoformat()}/{end.isoformat()}"
    url = f"{_BASE}/details/biorxiv/{interval}/0"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("bioRxiv keyword search failed: %s", e)
            return []
    records: list[PaperRecord] = []
    query_lower = query.lower()
    for entry in payload.get("collection", [])[:200]:
        title = (entry.get("title") or "").strip()
        abstract = entry.get("abstract") or ""
        if not title:
            continue
        if query_lower not in title.lower() and query_lower not in abstract.lower():
            continue
        rec = _build_record(entry)
        records.append(rec)
        if len(records) >= max_results:
            break
    return records


async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None:
    url = f"{_BASE}/details/biorxiv/{doi}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            payload = r.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("bioRxiv DOI fetch failed: %s", e)
            return None
    entries = payload.get("collection", [])
    if not entries:
        return None
    return _build_record(entries[0])


def _build_record(entry: dict) -> PaperRecord:
    title = (entry.get("title") or "").strip()
    doi = (entry.get("doi") or "").strip().lower() or None
    abstract = entry.get("abstract") or None
    pdf_url = None
    if doi:
        pdf_url = f"https://www.biorxiv.org/content/{doi}.full.pdf"
    pub_date = None
    pub_str = entry.get("date")
    if pub_str:
        try:
            pub_date = date.fromisoformat(pub_str)
        except ValueError:
            pub_date = None
    authors: list[dict] = []
    for raw in (entry.get("authors") or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        if "," in raw:
            family, _, given = raw.partition(",")
            authors.append({"family": family.strip(), "given": given.strip()})
        else:
            tokens = raw.split()
            if len(tokens) >= 2:
                authors.append({"family": tokens[-1], "given": " ".join(tokens[:-1])})
            else:
                authors.append({"family": tokens[0], "given": ""})
    return PaperRecord(
        source=name,
        title=title,
        authors=authors,
        doi=doi,
        biorxiv_id=doi,
        journal="bioRxiv",
        publication_date=pub_date,
        abstract=abstract,
        pdf_url=pdf_url,
        has_full_text=bool(pdf_url),
    )
