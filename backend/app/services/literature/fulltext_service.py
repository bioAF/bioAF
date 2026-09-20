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
    # change_7.5 section 2.4: the methods paragraphs and the figure and table captions, addressable,
    # so a claim is bound with the legend it cites and the methods of its experiment. Kept for the
    # read only; the full text is never persisted.
    sections: dict = field(default_factory=lambda: {"methods": [], "captions": {}})


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


def _flat(node) -> str:
    return _WHITESPACE_RE.sub(" ", " ".join(t.strip() for t in node.itertext() if t and t.strip())).strip()


_METHODS_TITLE = re.compile(r"\b(?:methods?|materials)\b", re.IGNORECASE)
_CAPTION_LABEL = re.compile(r"^\s*(?P<kind>fig(?:ure)?|table)\.?\s*(?P<n>S?\d+)", re.IGNORECASE)

# plan_8_6 section 3: which kind of section a title names, so the evidence index can prefer the
# article's own structure over headings guessed from flattened prose. An unrecognised title is
# `other`, which is indexed and rankable: a paper that writes its methods under a heading nobody
# recognises still has its methods read.
_SECTION_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    ("methods", re.compile(r"\b(?:methods?|materials|experimental procedures?|star methods)\b", re.I)),
    ("results", re.compile(r"\bresults?\b|\bfindings\b", re.I)),
    ("introduction", re.compile(r"\bintroduction\b|\bbackground\b", re.I)),
    ("discussion", re.compile(r"\bdiscussion\b|\bconclusions?\b|\blimitations?\b", re.I)),
    ("abstract", re.compile(r"\babstract\b|\bsummary\b", re.I)),
    (
        "availability",
        re.compile(r"\b(?:data|code|software|materials?)\s+availability\b|\baccession\b|\bresource sharing\b", re.I),
    ),
)


def _section_kind(sec_type: str, title: str, inherited: str | None) -> str:
    """The kind of a section, from its ``sec-type`` or its title, else the kind it sits inside."""
    for kind, pattern in _SECTION_KINDS:
        if pattern.search(sec_type or "") or pattern.search(title or ""):
            return kind
    return inherited or "other"


def _jats_sections(xml_text: str) -> dict:
    """A JATS article's sections, addressable: its methods paragraphs, its legends and its structure.

    ``{"methods": [paragraph, ...], "captions": {"figure 1": "...", "table 2": "..."},
    "index": [{"title", "kind", "paragraphs"}, ...]}``. A methods section is one whose ``sec-type``
    or title says so; its nested sections' paragraphs are its own.

    plan_8_6 section 3: ``index`` is the whole document by section, in document order, each entry
    keeping the title the authors wrote. A methods subsection ("Bulk RNA-seq analysis") is its own
    entry, so a passage can say which part of the methods it came from, and the results, the
    discussion and the availability statement are no longer left to be guessed from flattened prose.
    Never raises: unparseable markup has no sections.
    """
    empty: dict = {"methods": [], "captions": {}, "index": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return empty
    methods: list[str] = []
    index: list[dict] = []
    for sec, inherited in _walk_sections(root):
        sec_type = next((v for k, v in sec.attrib.items() if k.endswith("sec-type")), "") or ""
        title = next((c for c in sec if _local_name(c.tag) == "title"), None)
        title_text = _flat(title) if title is not None else ""
        kind = _section_kind(sec_type, title_text, inherited)
        # Only this section's OWN paragraphs: a nested subsection is its own entry, so a paragraph
        # is never indexed twice under a parent and a child heading.
        own = [text for text in (_flat(p) for p in _own_paragraphs(sec)) if text]
        if own:
            index.append({"title": title_text, "kind": kind, "paragraphs": own})
        if kind == "methods":
            methods.extend(text for text in own if text not in methods)
    captions: dict[str, str] = {}
    for node in root.iter():
        if _local_name(node.tag) not in ("fig", "table-wrap"):
            continue
        label = next((c for c in node if _local_name(c.tag) == "label"), None)
        caption = next((c for c in node if _local_name(c.tag) == "caption"), None)
        match = _CAPTION_LABEL.match(_flat(label)) if label is not None else None
        if match and caption is not None:
            kind = "table" if match.group("kind").lower().startswith("t") else "figure"
            key = f"{kind} {match.group('n').lower()}"
            if key not in captions:
                captions[key] = _flat(caption)
                index.append({"title": key, "kind": "legend", "paragraphs": [captions[key]]})
    return {"methods": methods, "captions": captions, "index": index}


def _walk_sections(root, node=None, inherited: str | None = None):
    """Every ``sec`` in document order, each with the kind of the section it sits inside."""
    parent = root if node is None else node
    for child in parent:
        if _local_name(child.tag) != "sec":
            yield from _walk_sections(root, child, inherited)
            continue
        sec_type = next((v for k, v in child.attrib.items() if k.endswith("sec-type")), "") or ""
        title = next((c for c in child if _local_name(c.tag) == "title"), None)
        kind = _section_kind(sec_type, _flat(title) if title is not None else "", inherited)
        yield child, inherited
        yield from _walk_sections(root, child, kind)


def _own_paragraphs(sec):
    """A section's own ``p`` elements: not a nested subsection's, and not a figure legend's."""
    for child in sec:
        name = _local_name(child.tag)
        if name == "p":
            yield child
        elif name not in ("sec", "fig", "table-wrap"):
            yield from _own_paragraphs(child)


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
            sections=_jats_sections(xml_text),
        )
