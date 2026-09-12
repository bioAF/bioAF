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
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import httpx

from app.services.supplement_inventory import parse_jats_supplements

logger = logging.getLogger("bioaf.literature.fulltext")

_WHITESPACE_RE = re.compile(r"\s+")

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TIMEOUT = 30.0


@dataclass
class FullTextResult:
    """Normalized full text plus the provenance of where it came from."""

    text: str
    source: str  # the fetch route, e.g. "europepmc"
    external_id: str  # the resolved id the text was pulled from, e.g. "PMC3258391"
    # change_7.1 section 2: the article's supplement manifest, taken from the SAME document the
    # body text came from. The JATS names S1, S2 and S3 in its prose and attaches its media
    # elements; flattening to text and discarding the markup threw both away, and the supplements
    # were then never looked for anywhere else.
    supplements: list[dict] = field(default_factory=list)


def _jats_to_text(xml_text: str) -> str:
    """Flatten a JATS full-text document to plain text: the article ``<body>``, then its back matter.

    change_7.5 section 1.5: data availability, code availability and acknowledgments usually sit in
    ``<back>``, and reading ``<body>`` alone lost the statements that say where a paper's data are. The
    reference list is left out: it names other papers' deposits, and a scan of it would list them as
    this paper's own.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Europe PMC full text: could not parse JATS XML: %s", exc)
        return ""
    # {*} matches the body element in any namespace (or none), which JATS documents vary on.
    body = root.find(".//{*}body")
    parts = [body] if body is not None else [root]
    back = root.find(".//{*}back") if body is not None else None
    if back is not None:
        parts.extend(child for child in back if not _local_name(child.tag) == "ref-list")
    # ``itertext`` already yields tag-free, entity-decoded plain text, so only whitespace needs
    # normalizing. Do NOT run the abstract/title HTML sanitizer here: its ``<[^>]+>`` tag-stripper
    # treats the span between a literal ``<`` and the next ``>`` in statistical prose (P < 0.05,
    # Q-value < 1E-10, enrichment scores > 1.5) as a tag and deletes it, which routinely swallows the
    # data-availability accession the reproduction extractor exists to read.
    text = " ".join(t.strip() for node in parts for t in node.itertext() if t and t.strip())
    return _WHITESPACE_RE.sub(" ", text).strip()


def _local_name(tag: str) -> str:
    """An element's name without its namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


async def _resolve_open_access_id(
    client: httpx.AsyncClient, doi: str | None, pmid: str | None, pmcid: str | None
) -> str | None:
    """Resolve an identifier to an open-full-text EPMC ``PMCID`` (prefix included), or None.

    Only open-access PMC records expose ``fullTextXML``; a paper that is merely indexed (abstract
    only) resolves to None so the caller falls back to a pasted body."""
    if pmcid:
        return pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"

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
        return resolved_pmcid
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
                ext_id = await _resolve_open_access_id(client, doi, pmid, pmcid)
                if ext_id is None:
                    return None
                # Europe PMC keys full text by the bare PMCID: {BASE}/{PMCID}/fullTextXML. There is no
                # extra source path segment; {BASE}/PMC/{PMCID}/fullTextXML 404s.
                r = await client.get(f"{_BASE}/{ext_id}/fullTextXML")
                r.raise_for_status()
                xml_text = r.text
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Europe PMC full-text fetch failed: %s", exc)
                return None

        text = _jats_to_text(xml_text)
        if not text:
            return None
        return FullTextResult(
            text=text,
            source="europepmc",
            external_id=ext_id,
            supplements=parse_jats_supplements(xml_text),
        )
