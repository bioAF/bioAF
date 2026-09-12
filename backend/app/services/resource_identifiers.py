"""change_7.5 section 1.5: the identifiers a paper's text names, found deterministically.

The model's accession list alone decided what a paper named, so two reads of the same paper named
different things: study 38 extracted `GSE144396, PXD016781`, and study 37 extracted those plus three PDB
structures. The patterns below are scanned over the whole text the reader holds (the body and the back
matter, never the reference list), and their result is unioned with the model's list. Neither decides
alone.

A pattern recognises an identifier's form, and says nothing about whether bioAF can use it: PRIDE, PDB
and the rest are listed so the report can say bioAF has no adapter for them.
"""

from __future__ import annotations

import re

# Deposit-level identifiers, by archive. A sample (GSM, SRR, SRX) belongs to its deposit and is not a
# resource of its own; `archive_discovery.is_sample_accession` makes the same distinction.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("geo", re.compile(r"(?<![A-Za-z0-9])GSE\d{3,}(?![A-Za-z0-9])")),
    ("sra", re.compile(r"(?<![A-Za-z0-9])(?:PRJNA|PRJEB|PRJDB|SRP|ERP|DRP)\d{3,}(?![A-Za-z0-9])")),
    ("ega", re.compile(r"(?<![A-Za-z0-9])EGA[SD]\d{11}(?![A-Za-z0-9])")),
    ("arrayexpress", re.compile(r"(?<![A-Za-z0-9])E-[A-Z]{4}-\d+(?![A-Za-z0-9])")),
    ("pride", re.compile(r"(?<![A-Za-z0-9])PXD\d{6}(?![A-Za-z0-9])")),
    ("massive", re.compile(r"(?<![A-Za-z0-9])MSV\d{9}(?![A-Za-z0-9])")),
    ("emdb", re.compile(r"(?<![A-Za-z0-9])EMD-\d{4,5}(?![A-Za-z0-9])")),
    ("zenodo", re.compile(r"10\.5281/zenodo\.\d+", re.I)),
    ("figshare", re.compile(r"10\.6084/m9\.figshare\.\d+(?:\.v\d+)?", re.I)),
    ("github", re.compile(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.I)),
    ("gitlab", re.compile(r"https?://(?:www\.)?gitlab\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.I)),
)

# A PDB entry is a digit and three alphanumerics, which also spells a year or a cell line. It is read
# as one only within a short window after a "PDB" token ("PDB accession codes 6LUI, 6LUJ and 6LUK").
_PDB_CONTEXT = re.compile(r"\bPDB\b[^.;\n]{0,80}", re.I)
_PDB_CODE = re.compile(r"(?<![A-Za-z0-9])([1-9][A-Za-z0-9]{3})(?![A-Za-z0-9])")


def _pdb_codes(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for window in _PDB_CONTEXT.finditer(text):
        for code in _PDB_CODE.finditer(window.group(0)):
            token = code.group(1)
            # A code with no letter is a number, not an entry.
            if any(ch.isalpha() for ch in token[1:]):
                found.append((window.start() + code.start(1), token.upper()))
    return found


def scan_identifiers(text: str | None) -> list[dict]:
    """Every identifier ``text`` names, once each, in order of first mention.

    ``[{"identifier": "PXD016781", "archive": "pride"}, ...]``. A URL keeps its own spelling with any
    trailing punctuation removed.
    """
    body = text or ""
    found: list[tuple[int, str, str]] = []
    for archive, pattern in _PATTERNS:
        for match in pattern.finditer(body):
            identifier = match.group(0).rstrip(".,;:)")
            if archive in ("zenodo", "figshare"):
                identifier = identifier.lower()
            found.append((match.start(), identifier, archive))
    found.extend((at, code, "pdb") for at, code in _pdb_codes(body))

    seen: set[str] = set()
    ordered: list[dict] = []
    for _at, identifier, archive in sorted(found):
        key = identifier.upper()
        if key in seen:
            continue
        seen.add(key)
        ordered.append({"identifier": identifier, "archive": archive})
    return ordered
