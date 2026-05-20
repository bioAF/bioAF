"""PDF metadata extraction using PyMuPDF.

Pure helpers operate on raw PDF bytes. Network I/O lives in upload_service.
Extraction never modifies the input PDF; it returns a dict the caller writes
into the literature_papers row.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

logger = logging.getLogger("bioaf.literature.extraction")


# Match a DOI of the form 10.xxxx/yyyy. We do not chase encoded forms in v1.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)


def _normalize_doi(doi: str) -> str:
    cleaned = doi.strip().rstrip(".,;:)")
    return cleaned.lower()


def find_doi(text: str) -> str | None:
    """First DOI found in the text, or None."""
    m = _DOI_RE.search(text or "")
    if not m:
        return None
    return _normalize_doi(m.group(0))


def parse_authors(pdf_authors: str | None) -> list[dict]:
    """Convert a free-form 'Doe, John; Smith, Jane' string into ordered author
    dicts. PyMuPDF's metadata['author'] field is unstructured, so we accept
    semicolons or 'and' as separators and split family/given names on commas
    or whitespace."""
    if not pdf_authors:
        return []
    parts = re.split(r"\s*(?:;| and )\s*", pdf_authors)
    authors: list[dict] = []
    for raw in parts:
        if not raw.strip():
            continue
        if "," in raw:
            family, _, given = raw.partition(",")
            authors.append({"given": given.strip(), "family": family.strip()})
        else:
            tokens = raw.strip().split()
            if len(tokens) >= 2:
                authors.append({"given": " ".join(tokens[:-1]), "family": tokens[-1]})
            elif tokens:
                authors.append({"given": "", "family": tokens[0]})
    return authors


def extract_pdf_metadata(pdf_bytes: bytes) -> dict[str, Any]:
    """Open a PDF in-memory and return a metadata dict with the keys this
    feature consumes. All keys are optional; missing fields return None.

    The result dict can include:
      title, authors, doi, journal, publication_date, abstract,
      full_text (the first ~20 pages, each prefixed with a "[Page N]" marker so
      page boundaries survive into the stored blob),
      page_count.
    """
    result: dict[str, Any] = {
        "title": None,
        "authors": [],
        "doi": None,
        "journal": None,
        "publication_date": None,
        "abstract": None,
        "full_text": None,
        "page_count": 0,
    }
    try:
        import fitz  # type: ignore

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            result["page_count"] = doc.page_count
            meta = doc.metadata or {}
            if meta.get("title"):
                result["title"] = meta["title"].strip()
            if meta.get("author"):
                result["authors"] = parse_authors(meta["author"])
            if meta.get("creationDate"):
                # Format like "D:20240512..."
                m = re.search(r"D:(\d{4})(\d{2})(\d{2})", meta["creationDate"])
                if m:
                    y, mo, d = (int(x) for x in m.groups())
                    try:
                        result["publication_date"] = date(y, mo, d)
                    except ValueError:
                        pass

            # Scan first two pages for DOI + abstract. Full text from first 20.
            # head_text is the raw concatenation used for DOI/abstract detection;
            # marked_pages carries a "[Page N]" delimiter before each page so the
            # stored full text preserves page boundaries and the Agent Review can
            # cite the page when full text is included (ADR-057 / spec-automation).
            head_pages = []
            marked_pages = []
            for i, page in enumerate(doc):
                page_text = page.get_text("text") or ""
                head_pages.append(page_text)
                marked_pages.append(f"[Page {i + 1}]\n{page_text}")
                if i >= 19:
                    break
            head_text = "\n".join(head_pages)
            if not result["doi"]:
                doi = find_doi(head_text[:50_000])
                if doi:
                    result["doi"] = doi
            if not result["abstract"]:
                result["abstract"] = _extract_abstract(head_text)
            result["full_text"] = "\n\n".join(marked_pages).strip() or None
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("PDF metadata extraction failed: %s", e)
    return result


_ABSTRACT_RE = re.compile(
    r"\b(?:abstract|summary)\b\s*[:\n]\s*(.{120,3000}?)(?:\n\s*(?:keywords|introduction|background|1\.|key words)\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_abstract(text: str) -> str | None:
    if not text:
        return None
    m = _ABSTRACT_RE.search(text[:20_000])
    if not m:
        return None
    candidate = m.group(1).strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if len(candidate) < 100:
        return None
    return candidate[:5000]
