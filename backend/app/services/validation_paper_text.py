"""plan_8_1 section 2.1, decision D7: where a paper read takes its text, and how it takes it again.

The read fetched Europe PMC by DOI or took pasted text, and kept neither. The Literature Library can
already hold a paper's full text (an uploaded PDF's extracted text, ``extracted_text_uri``), and agent
review reads it; the validation read never did, even for a study linked to that paper.

**The order (D7):** Europe PMC by DOI first, because it also supplies the supplement manifest and the
addressable sections; then the Library's stored text for the study's paper, when it has one; then pasted
text. The source and a hash of the text are recorded on the extraction cycle, so a later stage that needs
the text again (the finding inventory, its retry) reads it from the SAME source and can tell when it is no
longer the text the claims were read from. Nothing here stores the text itself.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.adapters.registry import get_storage_adapter

logger = logging.getLogger("bioaf.validation_paper_text")

EUROPE_PMC = "europe_pmc"
LIBRARY = "library"
PASTED = "pasted"

# plan_8_1 labels, pending the owner's sign-off: the "Paper text available" row names its source.
SOURCE_LABELS = {
    EUROPE_PMC: "Europe PMC",
    LIBRARY: "the Literature Library",
    PASTED: "text pasted into bioAF",
}


@dataclass
class PaperText:
    text: str
    source: str
    supplements: list
    pmcid: str = ""
    sections: dict | None = None

    def record(self) -> dict:
        """What the extraction cycle keeps about the text: never the text itself."""
        return {"source": self.source, "sha256": text_hash(self.text), "chars": len(self.text)}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _paper(session, study):
    if not getattr(study, "paper_id", None):
        return None
    from app.models.literature import LiteraturePaper

    return await session.get(LiteraturePaper, study.paper_id)


def _doi(study, paper) -> str | None:
    return (getattr(study, "source_doi", None) or getattr(paper, "doi", None) or "").strip() or None


async def from_europe_pmc(study, paper=None) -> PaperText | None:
    from app.services.literature.fulltext_service import FullTextFetchService

    doi = _doi(study, paper)
    if not doi:
        return None
    result = await FullTextFetchService.fetch(doi=doi)
    if result is None:
        return None
    return PaperText(
        text=result.text,
        source=EUROPE_PMC,
        supplements=list(result.supplements or []),
        pmcid=result.external_id or "",
        sections=getattr(result, "sections", None),
    )


async def from_library(session, study) -> PaperText | None:
    paper = await _paper(session, study)
    if paper is None or not paper.has_full_text or not paper.extracted_text_uri:
        return None
    try:
        text = await get_storage_adapter().read_text(paper.extracted_text_uri)
    except Exception as exc:  # noqa: BLE001 - an unreadable blob is a source that is not there
        logger.warning("study %s: the library's text for paper %s could not be read: %s", study.id, paper.id, exc)
        return None
    if not (text or "").strip():
        return None
    return PaperText(text=text, source=LIBRARY, supplements=[])


async def acquire(session, study, *, pasted: str | None) -> PaperText | None:
    """D7: Europe PMC, then the Library, then pasted text. None when none of them has the paper."""
    paper = await _paper(session, study)
    for found in (await from_europe_pmc(study, paper), await from_library(session, study)):
        if found is not None:
            return found
    if (pasted or "").strip():
        return PaperText(text=pasted or "", source=PASTED, supplements=[])
    return None


async def again(session, study, source: str | None, *, pasted: str | None = None) -> PaperText | None:
    """The text from the source a stage recorded. Pasted text was never kept, so it comes back only when
    it is pasted again, or when the Library now holds the paper's text."""
    if source == LIBRARY:
        return await from_library(session, study)
    if source == PASTED:
        if (pasted or "").strip():
            return PaperText(text=pasted or "", source=PASTED, supplements=[])
        return await from_library(session, study)
    return await from_europe_pmc(study, await _paper(session, study))
