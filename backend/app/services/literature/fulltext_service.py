"""B1: full-text acquisition for the reproduction extractor (lit_validation).

The lit-review sources return abstracts and metadata, but the methods, accessions, and
data-availability statements the reproduction extractor (B2) needs live in the article body. This
service fetches machine-readable full text via the Europe PMC REST backbone: resolve the paper's
identifier to an open-access EPMC record, pull its JATS ``fullTextXML``, and normalize it to plain
text. Europe PMC was the one reliable route in spike-00 (naive publisher fetches were CAPTCHA/403/
auth-walled); it is the backbone here, and the seam is deliberately small so mirror/PDF fallbacks can
be layered on later.

Returns ``None`` (rather than raising) whenever full text is not openly reachable, so the caller can
fall back to a pasted-in body. Network egress lives only here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

from app.services.literature.sources import sanitize_source_text

logger = logging.getLogger("bioaf.literature.fulltext")

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TIMEOUT = 30.0


@dataclass
class FullTextResult:
    """Normalized full text plus the provenance of where it came from."""

    text: str
    source: str  # the fetch route, e.g. "europepmc"
    external_id: str  # the resolved id the text was pulled from, e.g. "PMC3258391"


def _jats_to_text(xml_text: str) -> str:
    """Flatten a JATS full-text document to plain text, preferring the article ``<body>``."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Europe PMC full text: could not parse JATS XML: %s", exc)
        return ""
    # {*} matches the body element in any namespace (or none), which JATS documents vary on.
    node = root.find(".//{*}body")
    if node is None:
        node = root
    text = " ".join(t.strip() for t in node.itertext() if t and t.strip())
    return sanitize_source_text(text) or ""


async def _resolve_open_access_id(
    client: httpx.AsyncClient, doi: str | None, pmid: str | None, pmcid: str | None
) -> tuple[str, str] | None:
    """Resolve an identifier to an EPMC ``(source, ext_id)`` that has open full text, or None.

    Only open-access PMC records expose ``fullTextXML``; a paper that is merely indexed (abstract
    only) resolves to None so the caller falls back to a pasted body."""
    if pmcid:
        normalized = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return ("PMC", normalized)

    if doi:
        query = f'DOI:"{doi}"'
    elif pmid:
        query = f"EXT_ID:{pmid} AND SRC:MED"
    else:
        return None

    r = await client.get(
        f"{_BASE}/search",
        params={"query": query, "format": "json", "resultType": "core", "pageSize": "1"},
    )
    r.raise_for_status()
    results = r.json().get("resultList", {}).get("result", [])
    if not results:
        return None
    item = results[0]
    resolved_pmcid = (item.get("pmcid") or "").strip()
    if resolved_pmcid and item.get("inEPMC") == "Y":
        return ("PMC", resolved_pmcid)
    return None


class FullTextFetchService:
    @staticmethod
    async def fetch(
        *, doi: str | None = None, pmid: str | None = None, pmcid: str | None = None
    ) -> FullTextResult | None:
        """Fetch and normalize a paper's full text from Europe PMC, or None if not openly reachable."""
        if not (doi or pmid or pmcid):
            return None

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                resolved = await _resolve_open_access_id(client, doi, pmid, pmcid)
                if resolved is None:
                    return None
                source, ext_id = resolved
                r = await client.get(f"{_BASE}/{source}/{ext_id}/fullTextXML")
                r.raise_for_status()
                xml_text = r.text
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Europe PMC full-text fetch failed: %s", exc)
                return None

        text = _jats_to_text(xml_text)
        if not text:
            return None
        return FullTextResult(text=text, source="europepmc", external_id=ext_id)
