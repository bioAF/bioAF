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
from app.services.literature.fulltext_service import FullTextFetchService

logger = logging.getLogger("bioaf.validation_paper_text")

EUROPE_PMC = "europe_pmc"
LIBRARY = "library"
PASTED = "pasted"
ROUTES = (EUROPE_PMC, LIBRARY, PASTED)

# plan_8_3 section 1.4: what one source's attempt actually established. Study 49's read failed at
# Europe PMC and was recorded as having no text; the account of that ONE source was accurate and it
# is not a fact about the paper, because two other routes were never reported as still open.
SUCCEEDED = "succeeded"
# bioAF could not reach the endpoint, or it answered with an error. Another attempt could fix it.
ENDPOINT_FAILED = "endpoint_failed"
# The API answered and holds no open full text for this paper. A reuse fact about this source, and
# never a statement about whether the paper's results or data are available anywhere.
NOT_OPEN_ACCESS = "not_open_access"
# There was nothing to look the paper up by.
NO_IDENTIFIER = "no_identifier"
# The Library holds no stored text for this paper.
NO_STORED_TEXT = "no_stored_text"
# No text was supplied.
NOT_SUPPLIED = "not_supplied"
OUTCOMES = (SUCCEEDED, ENDPOINT_FAILED, NOT_OPEN_ACCESS, NO_IDENTIFIER, NO_STORED_TEXT, NOT_SUPPLIED)

_OUTCOME_WORDS = {
    ENDPOINT_FAILED: "bioAF could not reach Europe PMC",
    NOT_OPEN_ACCESS: "Europe PMC holds no open full text for this paper",
    NO_IDENTIFIER: "the study names no identifier to look the paper up by",
    NO_STORED_TEXT: "the Literature Library holds no stored text for this paper",
    NOT_SUPPLIED: "no text was supplied to bioAF",
}
# Words pending the owner's sign-off.
NOT_ESTABLISHED_NOTE = (
    "This does not establish that the paper's text, results or data are unavailable; it says what these "
    "sources answered."
)
SUPPLEMENT_LIMITATION = (
    "This route does not supply the paper's supplement manifest, so what supplements it publishes is not "
    "established here."
)

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

    @property
    def supplements_established(self) -> bool:
        """Whether this route established WHAT the paper publishes as supplements.

        plan_8_3 section 1.4: only Europe PMC carries the manifest. A text supplied by hand or read
        back from the Library comes with an empty list because the route supplies none, which is not
        the same fact as a paper that published none, and nothing may read it as one.
        """
        return self.source == EUROPE_PMC

    @property
    def supplement_limitation(self) -> str | None:
        return None if self.supplements_established else SUPPLEMENT_LIMITATION

    def record(self) -> dict:
        """What the extraction cycle keeps about the text: never the text itself."""
        return {
            "source": self.source,
            "sha256": text_hash(self.text),
            "chars": len(self.text),
            "supplements_established": self.supplements_established,
            "supplement_limitation": self.supplement_limitation,
        }


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _paper(session, study):
    if not getattr(study, "paper_id", None):
        return None
    from app.models.literature import LiteraturePaper

    return await session.get(LiteraturePaper, study.paper_id)


def _doi(study, paper) -> str | None:
    return (getattr(study, "source_doi", None) or getattr(paper, "doi", None) or "").strip() or None


async def try_europe_pmc(study, paper=None) -> tuple[PaperText | None, dict]:
    """The text and an account of the attempt: which failure it was, in Europe PMC's own terms.

    plan_8_3 section 1.4: an endpoint that did not answer and a paper that has no open full text are
    different facts. The first is bioAF's reach and another attempt could fix it; the second is this
    source's reuse policy, and neither says anything about whether the paper's results exist.
    """
    import httpx

    doi = _doi(study, paper)
    if not doi:
        return None, _attempt(EUROPE_PMC, NO_IDENTIFIER, None)
    try:
        result = await FullTextFetchService.fetch(doi=doi)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.info("study %s: Europe PMC could not be reached: %s", getattr(study, "id", "?"), exc)
        return None, _attempt(EUROPE_PMC, ENDPOINT_FAILED, str(exc)[:300])
    if result is None:
        return None, _attempt(EUROPE_PMC, NOT_OPEN_ACCESS, f"no open full text for {doi}")
    return (
        _paper_text(result),
        _attempt(EUROPE_PMC, SUCCEEDED, result.external_id or None),
    )


def _attempt(source: str, outcome: str, detail: str | None) -> dict:
    return {"source": source, "outcome": outcome, "detail": detail}


def acquisition_record(attempts: list[dict], *, text_source: str | None) -> dict:
    """What every route answered, whether the text was established, and which routes are still open.

    plan_8_3 section 1.4: a study whose read failed at one source is not a study whose paper cannot be
    read. The routes that were never tried are named, so the next step is a step somebody can take.
    """
    rows = [a for a in attempts or [] if isinstance(a, dict)]
    tried = {a.get("source") for a in rows}
    established = bool(text_source) or any(a.get("outcome") == SUCCEEDED for a in rows)
    remaining = [] if established else [r for r in ROUTES if r not in tried]
    failures = [
        _OUTCOME_WORDS.get(str(a.get("outcome")), str(a.get("outcome"))) for a in rows if a.get("outcome") != SUCCEEDED
    ]
    reason = None
    if not established:
        reason = "; ".join(dict.fromkeys(failures)) or "no source was tried"
        reason = f"{reason}. {NOT_ESTABLISHED_NOTE}"
    return {
        "established": established,
        "source": text_source,
        "attempts": rows,
        "routes_remaining": remaining,
        "reason": reason,
    }


def _paper_text(result) -> PaperText:
    return PaperText(
        text=result.text,
        source=EUROPE_PMC,
        supplements=list(result.supplements or []),
        pmcid=result.external_id or "",
        sections=getattr(result, "sections", None),
    )


async def from_europe_pmc(study, paper=None) -> PaperText | None:
    """The text, or None. Kept for callers that need only the answer; the attempt is on
    ``try_europe_pmc``."""
    found, _ = await try_europe_pmc(study, paper)
    return found


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


async def acquire(session, study, *, pasted: str | None, attempts: list | None = None) -> PaperText | None:
    """D7: Europe PMC, then the Library, then pasted text. None when none of them has the paper.

    plan_8_3 section 1.4: ``attempts``, when given, collects an account of every route tried, so a
    failed read can say which source refused it and which routes are still open.
    """
    record = attempts if attempts is not None else []
    paper = await _paper(session, study)
    found, attempt = await try_europe_pmc(study, paper)
    record.append(attempt)
    if found is not None:
        return found
    library = await from_library(session, study)
    record.append(_attempt(LIBRARY, SUCCEEDED if library else NO_STORED_TEXT, None))
    if library is not None:
        return library
    if (pasted or "").strip():
        record.append(_attempt(PASTED, SUCCEEDED, None))
        return PaperText(text=pasted or "", source=PASTED, supplements=[])
    record.append(_attempt(PASTED, NOT_SUPPLIED, None))
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
