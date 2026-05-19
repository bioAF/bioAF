"""PubMed (NCBI E-utilities) adapter."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from app.services.literature.sources import PaperRecord, RateLimit

logger = logging.getLogger("bioaf.literature.pubmed")

name = "pubmed"

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TIMEOUT = 30.0


def get_rate_limit(has_api_key: bool) -> RateLimit:
    return RateLimit(requests_per_second=10.0 if has_api_key else 3.0)


async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]:
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(max_results)}
    if api_key:
        params["api_key"] = api_key
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_BASE}/esearch.fcgi", params=params)
        r.raise_for_status()
        data = r.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        # Fetch metadata for the returned PMIDs.
        fetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if api_key:
            fetch_params["api_key"] = api_key
        fr = await client.get(f"{_BASE}/efetch.fcgi", params=fetch_params)
        fr.raise_for_status()
        return _parse_efetch_xml(fr.text)


async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None:
    params = {"db": "pubmed", "term": f"{doi}[doi]", "retmode": "json"}
    if api_key:
        params["api_key"] = api_key
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(f"{_BASE}/esearch.fcgi", params=params)
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None
        fetch_params = {"db": "pubmed", "id": ids[0], "retmode": "xml"}
        if api_key:
            fetch_params["api_key"] = api_key
        fr = await client.get(f"{_BASE}/efetch.fcgi", params=fetch_params)
        fr.raise_for_status()
        results = _parse_efetch_xml(fr.text)
        return results[0] if results else None


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    text = "".join(el.itertext()).strip()
    return text or None


def _parse_efetch_xml(xml_text: str) -> list[PaperRecord]:
    results: list[PaperRecord] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("PubMed efetch parse error: %s", e)
        return results
    for article in root.findall(".//PubmedArticle"):
        title = _text(article.find(".//ArticleTitle")) or ""
        if not title:
            continue
        pmid = _text(article.find(".//PMID"))
        doi = None
        for aid in article.findall(".//ArticleId"):
            if aid.get("IdType", "").lower() == "doi":
                doi = (aid.text or "").strip().lower() or None
                break
        abstract_parts = []
        for ab in article.findall(".//Abstract/AbstractText"):
            t = _text(ab)
            if t:
                abstract_parts.append(t)
        abstract = "\n".join(abstract_parts) or None

        authors: list[dict] = []
        for au in article.findall(".//AuthorList/Author"):
            family = _text(au.find("LastName")) or ""
            given = _text(au.find("ForeName")) or _text(au.find("Initials")) or ""
            if family or given:
                authors.append({"family": family, "given": given})

        journal = _text(article.find(".//Journal/Title")) or _text(article.find(".//ISOAbbreviation"))
        pub_date = _parse_pubmed_date(article)
        results.append(
            PaperRecord(
                source=name,
                title=title,
                authors=authors,
                doi=doi,
                pmid=pmid,
                journal=journal,
                publication_date=pub_date,
                abstract=abstract,
            )
        )
    return results


def _parse_pubmed_date(article: ET.Element) -> date | None:
    pub = article.find(".//PubDate")
    if pub is None:
        return None
    year = _text(pub.find("Year"))
    if not year:
        return None
    try:
        y = int(year)
    except ValueError:
        return None
    month_name = _text(pub.find("Month")) or "1"
    day = _text(pub.find("Day")) or "1"
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    try:
        month = int(month_name)
    except ValueError:
        month = months.get(month_name[:3].lower(), 1)
    try:
        return date(y, month, int(day))
    except ValueError:
        return date(y, 1, 1)
