"""plan_8_6 section 3: the paper's text, indexed by section, once per source revision.

`validation_passages.method_statements` kept at most 40 keyword-matched sentences in document order,
each cut at 400 characters. On study 65 the regex matched narrative prose in the introduction and
the results ("differentiating", "sorted", "cloning") and spent its budget near the start of the
cell-culture methods, before the sequencing and computational procedures. The assessor then said the
paper lacked sequencing, preprocessing and statistical information the paper actually contains.

**The Methods section is covered, not sampled.** A budget that truncates it mid-section is a sampling
failure, not a bound. Everything available is indexed here; the per-obligation packet
(`validation_evidence_packets`) is where a bound is applied, and it selects for the obligation being
asked rather than by document order.

Each passage carries where it came from: the section as the document names it, the kind of section it
is, its place in the document, and whether the section assignment is certain. A JATS article supplies
its own structure; a PDF or pasted text has its headings inferred, and those passages say so.

Pure: it is given text and returns a record. No network, no model, no database.
"""

from __future__ import annotations

import hashlib
import re

INDEX_VERSION = 1

# The kinds of section an obligation's packet reasons about. Anything bioAF cannot place is `other`,
# which is indexed and rankable: a paper that writes its methods under a heading nobody recognises
# still has its methods read.
METHODS = "methods"
RESULTS = "results"
INTRODUCTION = "introduction"
DISCUSSION = "discussion"
ABSTRACT = "abstract"
LEGEND = "legend"
AVAILABILITY = "availability"
OTHER = "other"

# A passage is a paragraph, or a sentence-bounded part of one where the paragraph is long. Nothing is
# cut mid-sentence and nothing is dropped: a long paragraph becomes several passages with their own
# ids, in order, so a citation still resolves to one thing a reader can find.
MAX_PASSAGE_CHARS = 1200

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
_WHITESPACE = re.compile(r"\s+")

# Headings as a flattened document writes them, in the order a reader meets them. Matched on a word
# boundary and followed by the section's prose, because `_jats_to_text` normalizes away the markup
# that would otherwise say where a section begins.
_HEADINGS: tuple[tuple[str, str], ...] = (
    (ABSTRACT, r"Abstract"),
    (INTRODUCTION, r"Introduction|Background"),
    (
        METHODS,
        r"Materials and Methods|Methods and Materials|Material and Methods|STAR\s*Methods"
        r"|Experimental (?:Procedures|Methods|Model and Subject Details)|Online Methods"
        r"|Methods|Supplementary Methods|Supplemental Methods",
    ),
    (RESULTS, r"Results(?: and Discussion)?|Findings"),
    (DISCUSSION, r"Discussion|Conclusions?|Limitations(?: of the study)?"),
    (
        AVAILABILITY,
        r"Data (?:and code )?availability|Code availability|Data Availability Statement"
        r"|Accession (?:numbers?|codes?)",
    ),
)
_HEADING_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?]\s)|(?<=[.!?]\s\s))(?P<title>" + "|".join(p for _, p in _HEADINGS) + r")\b[:.]?\s+",
    re.IGNORECASE,
)
_KIND_FOR_HEADING = [(kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _HEADINGS]


def _normalized(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _kind_of(title: str) -> str:
    for kind, pattern in _KIND_FOR_HEADING:
        if pattern.fullmatch(title.strip().rstrip(":.")):
            return kind
    return OTHER


def _parts(paragraph: str) -> list[str]:
    """A paragraph as one passage, or as several sentence-bounded ones when it is long.

    Section 3: the 400-character truncation is removed from the assessment-evidence path. A long
    paragraph is carried in full, in pieces that each end at a sentence boundary.
    """
    text = _normalized(paragraph)
    if len(text) <= MAX_PASSAGE_CHARS:
        return [text] if text else []
    found: list[str] = []
    current = ""
    for sentence in _sentences(text):
        if current and len(current) + 1 + len(sentence) > MAX_PASSAGE_CHARS:
            found.append(current)
            current = sentence
        elif current:
            current = f"{current} {sentence}"
        else:
            current = sentence
        # A single sentence longer than the cap is kept whole rather than cut: a method written as
        # one long sentence is still that method.
        if len(current) >= MAX_PASSAGE_CHARS:
            found.append(current)
            current = ""
    if current:
        found.append(current)
    return found


def _sections_from_text(full_text: str) -> list[dict]:
    """Sections inferred from headings in flattened text, marked uncertain.

    Europe PMC supplies its own structure; a PDF extraction or pasted text does not, and reading such
    a paper as one undifferentiated blob is how a computational obligation came to be judged on
    culture protocol. An inferred assignment is recorded as inferred.
    """
    text = _normalized(full_text)
    if not text:
        return []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [{"title": "", "kind": OTHER, "certain": False, "paragraphs": [text]}]
    found: list[dict] = []
    if matches[0].start() > 0:
        lead = text[: matches[0].start()].strip()
        if lead:
            found.append({"title": "", "kind": OTHER, "certain": False, "paragraphs": [lead]})
    for index, match in enumerate(matches):
        title = match.group("title").strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue
        found.append({"title": title, "kind": _kind_of(title), "certain": False, "paragraphs": [body]})
    return found


_KNOWN_KINDS = (METHODS, RESULTS, INTRODUCTION, DISCUSSION, ABSTRACT, LEGEND, AVAILABILITY, OTHER)


def _sections_from_markup(sections: dict) -> list[dict]:
    """The sections an article names for itself, preferred over any heading guessed from prose.

    ``index`` is the whole document by section, in order, as `_jats_sections` builds it. A route that
    predates it supplies only ``methods`` and ``captions``, and those are read the same way, so a
    study recorded before this still gets its methods and legends placed.
    """
    entries = [e for e in (sections.get("index") or []) if isinstance(e, dict)]
    if entries:
        found = []
        for entry in entries:
            paragraphs = [str(p) for p in entry.get("paragraphs") or [] if str(p).strip()]
            kind = str(entry.get("kind") or OTHER)
            if paragraphs:
                found.append(
                    {
                        "title": str(entry.get("title") or ""),
                        "kind": kind if kind in _KNOWN_KINDS else OTHER,
                        "certain": True,
                        "paragraphs": paragraphs,
                    }
                )
        return found
    found = []
    paragraphs = [str(p) for p in (sections.get("methods") or []) if str(p).strip()]
    if paragraphs:
        found.append({"title": "Methods", "kind": METHODS, "certain": True, "paragraphs": paragraphs})
    for label, caption in (sections.get("captions") or {}).items():
        if str(caption).strip():
            found.append({"title": str(label), "kind": LEGEND, "certain": True, "paragraphs": [str(caption)]})
    return found


def build_index(full_text: str | None, *, sections: dict | None, source: str | None) -> dict:
    """The paper's available text, by section, with a stable id for every passage.

    ``sections`` is the structure the text route supplied (`_jats_sections`): its methods paragraphs
    and its figure and table legends. Where it exists it is authoritative and its passages are
    ``certain``. The rest of the document is placed from its headings and is not.
    """
    text = _normalized(full_text or "")
    from_markup = _sections_from_markup(sections or {})
    placed: list[dict] = list(from_markup)
    # The flattened text contains the same methods and legends the markup placed. Indexing them
    # again under an inferred heading would put the same sentence in two sections and let a packet
    # spend its budget twice on one paragraph, so the structured text is removed before the rest of
    # the document has its headings read.
    remaining = text
    for entry in from_markup:
        for paragraph in entry["paragraphs"]:
            remaining = remaining.replace(_normalized(paragraph), " ")
    for entry in _sections_from_text(_normalized(remaining)):
        if any(p.strip() for p in entry["paragraphs"]):
            placed.append(entry)

    passages: list[dict] = []
    recorded: list[dict] = []
    for order, entry in enumerate(placed, start=1):
        carried = 0
        for paragraph_index, paragraph in enumerate(entry["paragraphs"], start=1):
            for part_index, part in enumerate(_parts(paragraph), start=1):
                if not part:
                    continue
                passages.append(
                    {
                        "id": f"p{len(passages) + 1}",
                        "text": part,
                        "section": entry["title"] or entry["kind"],
                        "kind": entry["kind"],
                        "certain": bool(entry["certain"]),
                        "location": (
                            f"paragraph {paragraph_index}"
                            + (f", part {part_index}" if part_index > 1 else "")
                            + (f" of {entry['title']}" if entry["title"] else "")
                        ),
                        "source": _source_words(entry),
                        "order": order,
                    }
                )
                carried += 1
        if carried:
            recorded.append(
                {"title": entry["title"], "kind": entry["kind"], "certain": bool(entry["certain"]), "passages": carried}
            )

    return {
        "index_version": INDEX_VERSION,
        "source": source,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
        "chars": len(text),
        "sections": recorded,
        "passages": passages,
    }


def _source_words(entry: dict) -> str:
    """How a passage is named to the assessor, so a citation resolves to a place in the paper."""
    kind, title = entry["kind"], (entry["title"] or "").strip()
    if kind == LEGEND:
        return f"the paper's legend for {title}" if title else "a figure or table legend"
    if kind == METHODS:
        return f"the paper's methods ({title})" if title and title.lower() != "methods" else "the paper's methods"
    if kind == RESULTS:
        return "the paper's results"
    if kind == DISCUSSION:
        return "the paper's discussion and limitations"
    if kind == INTRODUCTION:
        return "the paper's introduction"
    if kind == ABSTRACT:
        return "the paper's abstract"
    if kind == AVAILABILITY:
        return "the paper's data and code availability statement"
    return f"the paper ({title})" if title else "the paper"


def methods_paragraphs(index: dict | None) -> list[str]:
    """The methods paragraphs, for the deterministic readers that normalize cutoffs and references.

    Section 3 item 4: ``validation_methods_cutoffs`` read ``_jats_sections["methods"]``, which is
    empty for every paper bioAF did not get from Europe PMC. It reads this instead, so a paper whose
    text came from the Library or from a paste is not silently exempt from M2 and M4.
    """
    found: list[str] = []
    for passage in (index or {}).get("passages") or []:
        if passage.get("kind") != METHODS:
            continue
        text = str(passage.get("text") or "").strip()
        if text and text not in found:
            found.append(text)
    return found
