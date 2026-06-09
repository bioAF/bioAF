"""External Literature Source adapters (ADR-056).

Each adapter implements the same interface:

    name: str  # one of pubmed, biorxiv, europepmc, semanticscholar
    async def search(query: str, max_results: int, api_key: str | None) -> list[PaperRecord]
    async def fetch_by_doi(doi: str, api_key: str | None) -> PaperRecord | None
    def get_rate_limit(has_api_key: bool) -> RateLimit

PaperRecord is the normalized struct that maps to literature_papers columns.
Adapters never write to the database; the search coordinator does.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.exceptions import ValidationError


_TAG_RE = re.compile(r"<[^>]+>")


def sanitize_source_text(value: str | None) -> str | None:
    """Strip HTML tags and decode HTML entities from source-returned text.

    Sources sometimes embed inline markup (`<i>`, `<sup>`, `<sub>`) and
    HTML-entity-encode special characters (`&amp;`, `&lt;`, `&gt;`). When the
    payload ends up in JSON metadata or rendered as plain text we want the
    decoded, tag-free form so titles and abstracts read cleanly without a
    second pass on the frontend.
    """
    if value is None:
        return None
    decoded = html.unescape(value)
    # Some sources double-encode (`&amp;lt;sup&amp;gt;`); unescape twice to
    # cover that case before stripping tags.
    if "&" in decoded:
        decoded = html.unescape(decoded)
    cleaned = _TAG_RE.sub("", decoded)
    # Collapse runs of whitespace introduced by removed tags.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


@dataclass(frozen=True)
class RateLimit:
    """Per-source rate limit. Requests per second; v1 uses a simple semaphore."""

    requests_per_second: float


@dataclass
class PaperRecord:
    """Normalized paper metadata returned by a source adapter."""

    source: str
    title: str
    authors: list[dict] = field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    biorxiv_id: str | None = None
    journal: str | None = None
    publication_date: date | None = None
    abstract: str | None = None
    source_score: float | None = None
    source_url: str | None = None
    pdf_url: str | None = None
    has_full_text: bool = False


class LiteratureSource(Protocol):
    """Adapter protocol: every source module declares a module-level
    ``name`` string and implements the three coroutine functions below."""

    name: str

    async def search(self, query: str, max_results: int, api_key: str | None) -> list[PaperRecord]: ...

    async def fetch_by_doi(self, doi: str, api_key: str | None) -> PaperRecord | None: ...

    def get_rate_limit(self, has_api_key: bool) -> RateLimit: ...


from app.services.literature.sources import (  # noqa: E402
    biorxiv,
    europepmc,
    pubmed,
    semanticscholar,
)

ADAPTERS = {
    pubmed.name: pubmed,
    biorxiv.name: biorxiv,
    europepmc.name: europepmc,
    semanticscholar.name: semanticscholar,
}


def get_adapter(source_name: str):
    if source_name not in ADAPTERS:
        raise ValidationError(f"unknown literature source: {source_name}")
    return ADAPTERS[source_name]
