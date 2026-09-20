"""plan_8_6 section 7: the design facts a comparison rests on, read from the paper's own words.

Study 65 returned verified for BOTH halves of E2. Its rationales name comparators and assert their
appropriateness; they do not assess replication, clone selection or group sizes for the specific
inferences. The paper itself describes selected high-performing clones, one granulosa-like line for
the single-cell experiments, one mouse comparator replicate in Figure 5, and one sample per clone
and condition in Figure 4B, while its single-cell methods report two samples per time point each
pooling six ovaroids. "One line" is not "one sample", and neither fact reached the obligation.

**A comparator is not a design.** What E2.B is answered from is the experimental unit, the replicate
kind and count, pairing or blocking, the relevance of the comparator, and how clones or lines were
selected. Where a field is not in the paper's evidence it stays explicitly unknown, and an unknown
field is a reason for the obligation to be untested rather than a reason to guess.

**A small study is not automatically invalid.** A selected clone and a single line are facts about
what a design supports, not verdicts. This module reports them; the assessment decides what they
mean for the claim being made.

Pure: it is given an index and returns a record. No network, no model, no database.
"""

from __future__ import annotations

import re

from app.services.validation_evidence_index import DISCUSSION, LEGEND, METHODS, OTHER, RESULTS

SUMMARY_VERSION = 1

UNKNOWN = "unknown"
NOT_STATED = "not stated in the evidence bioAF holds"

# Where a design fact is written: the methods, the legends that state n, and the limitations the
# discussion admits. The results narrate; they are read last.
_SECTIONS = (METHODS, LEGEND, DISCUSSION, RESULTS, OTHER)

_BIOLOGICAL = re.compile(
    r"\bbiological(?:ly)?\s+(?:replicat\w*|independent)|\bindependent\s+(?:biological\s+)?experiments?\b", re.I
)
_TECHNICAL = re.compile(r"\btechnical\s+replicat\w*|\btechnical\s+duplicat\w*", re.I)
_REPLICATE_COUNT = re.compile(
    r"\b(?P<word>two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:\w+\s+){0,2}?replicat\w*", re.I
)
_UNIT = re.compile(
    r"\b(?:per|each)\s+(?P<unit>sample|clone|line|condition|group|animal|mouse|donor|time\s?point|replicate)\b",
    re.I,
)
_GROUP_SIZE = re.compile(
    r"\bn\s*=\s*\d+"
    r"|\b\d+\s+\w+s?\s+per\s+\w+"
    r"|\b(?:one|two|three|four|five|six|a single)\s+(?:\w+\s+){0,2}?"
    r"(?:sample|replicate|clone|line|animal|mouse|donor|ovaroid|embryo)s?\b",
    re.I,
)
# "paired-end reads" is a sequencing format, not a paired design, and "batch" is what a reagent lot
# is as often as what a blocking factor is. Each of these has to name the design thing it is.
_PAIRING = re.compile(
    r"\bpaired\b(?![- ]?end)|\bmatched\s+(?:samples?|controls?|pairs?|donors?)"
    r"|\bblock(?:ed|ing)\s+(?:design|factor|variable)|\blitter(?:mate)?s?\b|\brandomi[sz]\w*"
    r"|\bbatch\s+(?:effect|correction|was|were|as a)",
    re.I,
)
# Selecting a CLONE to carry into the analysis is a design fact; selecting with an antibiotic and
# picking colonies are culture steps. What makes it the first is that a named line or clone was the
# thing chosen, so the word has to sit beside one.
_CHOSEN = r"select\w*|chosen|carried forward|high[- ]performing|top[- ]performing|best[- ]performing|representative"
_CHOSEN_UNIT = r"clones?|lines?|cell lines?"
_SELECTION = re.compile(
    rf"\b(?:{_CHOSEN})\b(?:\W+\w+){{0,3}}?\W+(?:{_CHOSEN_UNIT})\b"
    rf"|\b(?:{_CHOSEN_UNIT})\b(?:\W+\w+){{0,3}}?\W+(?:were|was)\s+(?:{_CHOSEN})\b"
    rf"|\b(?:two|three|four|five|\d+)\s+(?:top|best)\s+(?:\w+\s+)?(?:{_CHOSEN_UNIT})\b",
    re.I,
)


_COMPARATOR = re.compile(
    r"\bcontrol\b|\buntreated\b|\bvehicle\b|\bwild[- ]?type\b|\bknockout\b|\bparental\b|\bno[- ]TF\b"
    r"|\bcompared (?:to|with)\b|\bversus\b|\brelative to\b|\breference (?:sample|line|cell)",
    re.I,
)
_MAX_QUOTES = 6
_MAX_QUOTE_CHARS = 700

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _passages(index: dict | None) -> list[dict]:
    """The passages a design fact is written in, methods and legends first.

    A design fact stated in the methods ("2 samples per time point") outranks the results narrating
    around it, and reading in document order let the narration fill the quota before the methods
    were reached.
    """
    rows = [p for p in (index or {}).get("passages") or [] if isinstance(p, dict) and p.get("kind") in _SECTIONS]
    return sorted(rows, key=lambda p: (_SECTIONS.index(p["kind"]), p.get("order", 0)))


def _first(passages: list[dict], pattern: re.Pattern) -> dict | None:
    for passage in passages:
        match = pattern.search(str(passage.get("text") or ""))
        if match:
            return {
                "quote": _around(str(passage["text"]), match),
                "id": passage.get("id"),
                "section": passage.get("section"),
            }
    return None


def _all(passages: list[dict], pattern: re.Pattern, limit: int = _MAX_QUOTES) -> list[dict]:
    found: list[dict] = []
    for passage in passages:
        match = pattern.search(str(passage.get("text") or ""))
        if not match:
            continue
        found.append(
            {"quote": _around(str(passage["text"]), match), "id": passage.get("id"), "section": passage.get("section")}
        )
        if len(found) >= limit:
            break
    return found


def _around(text: str, match: re.Match) -> str:
    """The sentence a design fact is stated in, so a reader can check it where it lives."""
    start = text.rfind(". ", 0, match.start())
    begin = start + 2 if start >= 0 else 0
    stop = text.find(". ", match.end())
    end = stop + 1 if stop >= 0 else len(text)
    return text[begin:end].strip()[:_MAX_QUOTE_CHARS]


def design_summary(index: dict | None) -> dict:
    """The design facts this paper states, and the ones it does not.

    ``{"experimental_unit", "replication", "pairing", "comparators", "selection", "group_sizes",
    "unknown", "facts"}``. Every field is present; a field the paper does not state carries
    ``unknown`` and is named in ``unknown``, because an assessor must be able to tell a design bioAF
    read from one bioAF could not find.
    """
    passages = _passages(index)
    unknown: list[str] = []

    biological = _first(passages, _BIOLOGICAL)
    technical = _first(passages, _TECHNICAL)
    count_row = _first(passages, _REPLICATE_COUNT)
    count = None
    if count_row:
        match = _REPLICATE_COUNT.search(count_row["quote"])
        word = (match.group("word") if match else "").lower()
        count = _WORD_NUMBERS.get(word) or (int(word) if word.isdigit() else None)
    replication = {
        "kind": "biological" if biological else ("technical" if technical else UNKNOWN),
        "count": count,
        "quote": (biological or technical or count_row or {}).get("quote"),
        "id": (biological or technical or count_row or {}).get("id"),
    }
    if replication["kind"] == UNKNOWN and replication["quote"] is None:
        unknown.append("replication")

    group_sizes = _all(passages, _GROUP_SIZE)

    # The experimental unit is read from a sentence that states a replicate or a group size, never
    # from any sentence with "per" in it: "the medium volume per well was 0.5 ml" is a culture
    # volume, and reporting it as the unit of an experiment would be a wrong fact, not a thin one.
    unit, unit_row = UNKNOWN, None
    for candidate in ([replication] if replication["quote"] else []) + group_sizes:
        match = _UNIT.search(str(candidate.get("quote") or ""))
        if match:
            unit, unit_row = match.group("unit").lower(), candidate
            break
    if unit == UNKNOWN:
        unknown.append("experimental_unit")
    experimental_unit = {"unit": unit, "quote": (unit_row or {}).get("quote"), "id": (unit_row or {}).get("id")}

    pairing_row = _first(passages, _PAIRING)
    pairing = {
        "stated": bool(pairing_row),
        "quote": (pairing_row or {}).get("quote"),
        "id": (pairing_row or {}).get("id"),
    }
    if not pairing_row:
        unknown.append("pairing")

    comparator_rows = _all(passages, _COMPARATOR)
    comparators = {"named": [row["quote"] for row in comparator_rows], "ids": [row["id"] for row in comparator_rows]}
    if not comparator_rows:
        unknown.append("comparators")

    selection_row = _first(passages, _SELECTION)
    selection = {
        "stated": bool(selection_row),
        "quote": (selection_row or {}).get("quote"),
        "id": (selection_row or {}).get("id"),
    }
    if not selection_row:
        unknown.append("selection")

    if not group_sizes:
        unknown.append("group_sizes")

    summary = {
        "summary_version": SUMMARY_VERSION,
        "experimental_unit": experimental_unit,
        "replication": replication,
        "pairing": pairing,
        "comparators": comparators,
        "selection": selection,
        "group_sizes": group_sizes,
        "unknown": unknown,
    }
    summary["facts"] = _facts(summary)
    return summary


def _facts(summary: dict) -> list[dict]:
    """One citable line per design fact, each saying what it is and where it was read."""
    rows: list[dict] = []

    def add(label: str, text: str, source_id=None) -> None:
        rows.append(
            {
                "id": f"design:{len(rows) + 1}",
                "kind": "design",
                "carries": "design",
                "source": "the design of the comparisons this paper claims",
                "text": f"{label}: {text}" + (f" [from passage {source_id}]" if source_id else ""),
            }
        )

    unit = summary["experimental_unit"]
    if unit["unit"] != UNKNOWN:
        add("Experimental unit", f"{unit['unit']}. Quote: {unit['quote']}", unit["id"])
    replication = summary["replication"]
    if replication["kind"] != UNKNOWN or replication["quote"]:
        add(
            "Replication",
            f"{replication['kind']}"
            + (f", {replication['count']} per group" if replication["count"] else "")
            + f". Quote: {replication['quote']}",
            replication["id"],
        )
    if summary["pairing"]["stated"]:
        add("Pairing or blocking", f"Quote: {summary['pairing']['quote']}", summary["pairing"]["id"])
    for index, quote in enumerate(summary["comparators"]["named"]):
        add("Comparator", f"Quote: {quote}", summary["comparators"]["ids"][index])
    if summary["selection"]["stated"]:
        add("Selection of clones or lines", f"Quote: {summary['selection']['quote']}", summary["selection"]["id"])
    for group in summary["group_sizes"]:
        add("Group size", f"Quote: {group['quote']}", group["id"])
    if rows and summary["unknown"]:
        # Beside the facts bioAF DID read, so an assessor weighing them can see what is missing.
        # On its own it is not evidence: a paper bioAF read nothing about has an empty packet and an
        # untested obligation, not one passage saying bioAF found nothing.
        add(
            "Not established",
            f"bioAF found no statement of: {', '.join(summary['unknown'])}. These are {NOT_STATED}, which is "
            "not the same fact as the design lacking them",
        )
    return rows


def design_facts(index: dict | None) -> list[dict]:
    """The design facts as evidence an obligation can be given and an answer can cite."""
    return design_summary(index)["facts"]
