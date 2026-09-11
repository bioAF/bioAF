"""change_7.3 section 7: bounded passages of the paper, kept at read time while the text is in hand.

Reconciliation never saw any of the paper: ``evidence["paper_text"]`` was read in one place and
written nowhere. So the paper's own statement that three TE biopsies were excluded after quality
control could not correct a claim recorded as "54 samples, post-QC", and when every attachment
failed to download there was nothing at all to reconcile against.

**Bounded, per change_7.1 decision 5.** The claim's source passage and a short list of the paper's
statements about excluded samples and quality control. Never the full text and never a table: a
20,000-word paper costs the same few kilobytes as a short one.
"""

from __future__ import annotations

import re

# Characters either side of a claim's own sentence, widened to whole sentences and capped.
CLAIM_WINDOW = 320
MAX_PASSAGE_CHARS = 900
MAX_STATEMENTS = 6
MAX_STATEMENT_CHARS = 400

# A statement about the analysed population: something was excluded, removed or failed QC, and what
# it was is a sample-like unit. "regions we otherwise excluded" is about the genome, not the samples.
_EXCLUSION = re.compile(
    r"\b(exclud\w*|remov\w*|discard\w*|did not pass|fail\w* (?:to pass )?(?:quality|qc)|filtered out|omitted|dropped)\b",
    re.I,
)
_POPULATION = re.compile(
    r"\b(samples?|biops(?:y|ies)|embryos?|cells?|patients?|donors?|librar(?:y|ies)|subjects?|replicates?|"
    r"specimens?|individuals?|participants?|animals?|mice)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def _normalized(text: str) -> str:
    return " ".join((text or "").split())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(_normalized(text)) if s.strip()]


def claim_passage(full_text: str, claim_text: str | None) -> str | None:
    """The passage around a claim's own sentence, widened to sentence boundaries. None when the text
    does not contain the claim: a passage from elsewhere would be evidence about something else."""
    text = _normalized(full_text)
    claim = _normalized(claim_text or "")
    if not text or not claim:
        return None
    lowered = text.lower()
    index = lowered.find(claim.lower())
    length = len(claim)
    if index < 0 and len(claim) > 60:
        index = lowered.find(claim[:60].lower())
        length = 60
    if index < 0:
        return None

    start = max(0, index - CLAIM_WINDOW)
    end = min(len(text), index + length + CLAIM_WINDOW)
    # Widen to whole sentences, so a passage never starts or ends mid-clause.
    boundary = text.rfind(". ", 0, start)
    start = boundary + 2 if boundary >= 0 else 0
    stop = text.find(". ", end)
    end = stop + 1 if stop >= 0 else len(text)
    passage = text[start:end].strip()
    if len(passage) > MAX_PASSAGE_CHARS:
        # Keep the claim itself in view: centre the cap on it.
        centre = index - start + length // 2
        half = MAX_PASSAGE_CHARS // 2
        lo = max(0, min(centre - half, len(passage) - MAX_PASSAGE_CHARS))
        passage = passage[lo : lo + MAX_PASSAGE_CHARS].strip()
    return passage


def population_statements(full_text: str) -> list[str]:
    """What the paper says about samples it excluded or that failed quality control, bounded."""
    found: list[str] = []
    for sentence in _sentences(full_text):
        if not (_EXCLUSION.search(sentence) and _POPULATION.search(sentence)):
            continue
        statement = sentence[:MAX_STATEMENT_CHARS].strip()
        if statement not in found:
            found.append(statement)
        if len(found) >= MAX_STATEMENTS:
            break
    return found


def paper_passages(full_text: str | None, claim_texts: list[str | None]) -> dict:
    """``{"claims": [{"claim_text", "passage"}], "statements": [...]}`` for ``evidence``."""
    text = full_text or ""
    return {
        "claims": [{"claim_text": claim, "passage": claim_passage(text, claim)} for claim in claim_texts],
        "statements": population_statements(text),
    }
