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

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

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
    # ``itertext`` already yields tag-free, entity-decoded plain text, so only whitespace needs
    # normalizing. Do NOT run the abstract/title HTML sanitizer here: its ``<[^>]+>`` tag-stripper
    # treats the span between a literal ``<`` and the next ``>`` in statistical prose (P < 0.05,
    # Q-value < 1E-10, enrichment scores > 1.5) as a tag and deletes it, which routinely swallows the
    # data-availability accession the reproduction extractor exists to read.
    text = " ".join(t.strip() for t in node.itertext() if t and t.strip())
    return _WHITESPACE_RE.sub(" ", text).strip()


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
        return FullTextResult(text=text, source="europepmc", external_id=ext_id)


# Most authoritative copy first; a location with no stated version is tried last.
_VERSION_PREFERENCE = ("publishedVersion", "acceptedVersion", "submittedVersion")
MAX_OPEN_ACCESS_ATTEMPTS = 3

# PyMuPDF does not support multithreaded use, so each acquired copy is parsed in its own
# process, which is killed if the caller stops waiting (a deadline or a cancelled request). At
# most MAX_CONCURRENT_PDF_PARSES run at once; a parse beyond that is refused, never queued.
MAX_CONCURRENT_PDF_PARSES = 2
_PARSE_WORKER_ARGS: tuple[str, ...] = ("-m", "app.services.literature.pdf_parse_worker")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_parses_running = 0


class PdfParseError(Exception):
    """The parse worker failed; the copy is unusable."""


class PdfParserBusy(Exception):
    """MAX_CONCURRENT_PDF_PARSES parses are already running."""


async def parse_pdf(pdf_bytes: bytes) -> dict:
    """Extracted metadata for a PDF (extraction.extract_pdf_metadata), parsed in a worker process."""
    global _parses_running
    if _parses_running >= MAX_CONCURRENT_PDF_PARSES:
        raise PdfParserBusy
    _parses_running += 1
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *_PARSE_WORKER_ARGS,
            cwd=_BACKEND_ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await proc.communicate(pdf_bytes)
        finally:
            if proc.returncode is None:
                proc.kill()
                await asyncio.shield(proc.wait())
    finally:
        _parses_running -= 1
    if proc.returncode != 0:
        raise PdfParseError(f"exit {proc.returncode}: {err.decode(errors='replace')[-500:]}")
    try:
        return json.loads(out)
    except ValueError as exc:
        raise PdfParseError(f"unreadable worker output: {exc}") from exc


@dataclass
class OpenAccessPdf:
    pdf_bytes: bytes
    extracted: dict
    location: dict  # the OpenAlex open-access location the copy came from


async def fetch_open_access_pdf(oa_locations: list[dict]) -> OpenAccessPdf | None:
    """Download the best usable PDF among open-access locations, or None.

    Tries at most MAX_OPEN_ACCESS_ATTEMPTS distinct PDF URLs, best version first, through the
    SSRF-guarded lab-document downloader. A copy counts only if it is a PDF that parses; publishers
    often answer a PDF link with an HTML landing page. The caller bounds the total time.
    Raises PdfParserBusy when parsing is at capacity."""
    from app.exceptions import ValidationError
    from app.services.lab_document_upload_service import LabDocumentUploadService

    def rank(loc: dict) -> int:
        version = loc.get("version")
        return _VERSION_PREFERENCE.index(version) if version in _VERSION_PREFERENCE else len(_VERSION_PREFERENCE)

    urls: list[str] = []
    candidates = []
    for loc in sorted((loc for loc in oa_locations if loc.get("pdf_url")), key=rank):
        if loc["pdf_url"] not in urls:
            urls.append(loc["pdf_url"])
            candidates.append(loc)

    for loc in candidates[:MAX_OPEN_ACCESS_ATTEMPTS]:
        try:
            pdf_bytes, _, _ = await LabDocumentUploadService._fetch_url(loc["pdf_url"])
        except ValidationError as exc:
            logger.info("Open-access copy %s not usable: %s", loc["pdf_url"], exc)
            continue
        if not pdf_bytes.startswith(b"%PDF"):
            continue
        try:
            extracted = await parse_pdf(pdf_bytes)
        except PdfParseError as exc:
            logger.warning("Open-access copy %s could not be parsed: %s", loc["pdf_url"], exc)
            continue
        if not extracted.get("page_count"):
            continue
        return OpenAccessPdf(pdf_bytes=pdf_bytes, extracted=extracted, location=loc)
    return None


def copy_identity_problem(extracted: dict, expected_doi: str | None) -> str | None:
    """Why an acquired copy cannot be trusted as the paper with ``expected_doi``, or None.

    Only DOIs printed on the copy's first page count as its own; later pages carry references,
    so a paper that cites this one names its DOI there. A first page naming other DOIs is
    ``identity_mismatch``; one naming this DOI alongside others is ``identity_unconfirmed``. A
    first page with no DOI is not evidence against the copy, which OpenAlex lists for this work."""
    from app.services.literature.extraction import find_dois

    first_page = (extracted.get("full_text") or "").split("\n\n[Page 2]\n", 1)[0]
    dois = find_dois(first_page)
    if not expected_doi or not dois:
        return None
    if expected_doi not in dois:
        return "identity_mismatch"
    return "identity_unconfirmed" if len(dois) > 1 else None
