"""Naming Profile parser: read structured fields out of a filename.

The parser is pure: input is `(filename, profile)`; output is a dict shaped
``{"parsed": {field_name: value}, "unrecognized": [...], "warnings": [...]}``.
No DB / filesystem / network side effects.

The contract is restated in local/Naming Profiles/spec-parser.md. The high
points:

- Segments self-identify via 1-4 letter `identifier` prefix (or, for dates,
  by digit pattern). Segment order in the filename is irrelevant.
- Non-date values are returned as strings; leading zeros are preserved.
- Dates are returned as ISO `YYYY-MM-DD` strings regardless of input format.
- Identifier matching is case-insensitive; the authored case is preserved
  for display.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


# --- Token classification patterns ---------------------------------------
_NUMBER_TOKEN_RE = re.compile(r"^([A-Za-z]{1,4})(\d+)$")
_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_YYMMDD_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")
_YYYY_MM_DD_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_BARE_4 = re.compile(r"^\d{4}$")
_BARE_2 = re.compile(r"^\d{2}$")

_DOUBLE_EXT_SUFFIXES = (".gz", ".bz2", ".xz")


def _strip_extension(filename: str) -> str:
    """Drop the file extension, handling common double extensions (.fastq.gz)."""
    p = PurePosixPath(filename)
    if p.suffix.lower() in _DOUBLE_EXT_SUFFIXES and PurePosixPath(p.stem).suffix:
        return PurePosixPath(p.stem).stem
    return p.stem


def _inner_separator(delimiter: str) -> str:
    """The opposite of the profile delimiter, used to split string-segment values."""
    if delimiter == "_":
        return "-"
    if delimiter == "-":
        return "_"
    # Unknown delimiter: nothing to swap to. Falls back to a sentinel that
    # will never appear, so string-segment splits become no-ops.
    return "\x00"


def _normalize_yy(yy: str) -> str:
    """Two-digit year -> four-digit year using a fixed pivot.

    Per spec, the date format `YYMMDD` is rendered to ISO. Without a
    century pivot, `26` is ambiguous between 1926 and 2026. The pivot
    matches the Y2K-style cutoff most laboratory data systems use:
    `00..69` -> 2000s, `70..99` -> 1900s.
    """
    n = int(yy)
    return f"19{yy}" if n >= 70 else f"20{yy}"


def _try_classify_date(token: str, date_format: str | None) -> str | None:
    """Return ISO YYYY-MM-DD if the token matches the profile's date format."""
    if date_format == "YYYYMMDD":
        m = _YYYYMMDD_RE.match(token)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    elif date_format == "YYYY-MM-DD":
        m = _YYYY_MM_DD_RE.match(token)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    elif date_format == "YYMMDD":
        m = _YYMMDD_RE.match(token)
        if m:
            return f"{_normalize_yy(m.group(1))}-{m.group(2)}-{m.group(3)}"
    return None


def _find_date_segment(segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    for seg in segments:
        if seg.get("field_type") == "date":
            return seg
    return None


def _recombine_yyyy_mm_dd(tokens: list[str]) -> tuple[list[str], list[tuple[int, str]], list[str]]:
    """When delimiter == '-' and date format == 'YYYY-MM-DD', the date is
    shattered into three bare-digit tokens. Reassemble consecutive triples
    of `\\d{4}` + `\\d{2}` + `\\d{2}` into one ISO date string.

    Returns:
        (kept_tokens, date_hits, warnings)
        - kept_tokens: tokens that were NOT consumed by recombination.
        - date_hits: list of (start_index_in_original, iso_date) for each
          recombined triple. The caller binds the first to the profile's
          date segment; the rest are surfaced as warnings.
        - warnings: any messages produced (e.g. ambiguous date).
    """
    kept: list[str] = []
    date_hits: list[tuple[int, str]] = []
    warnings: list[str] = []

    i = 0
    while i < len(tokens):
        if (
            i + 2 < len(tokens)
            and _BARE_4.match(tokens[i])
            and _BARE_2.match(tokens[i + 1])
            and _BARE_2.match(tokens[i + 2])
        ):
            iso = f"{tokens[i]}-{tokens[i + 1]}-{tokens[i + 2]}"
            date_hits.append((i, iso))
            i += 3
        else:
            kept.append(tokens[i])
            i += 1

    if len(date_hits) > 1:
        warnings.append(
            f"ambiguous date: multiple matching triples; chose {date_hits[0][1]}"
        )

    return kept, date_hits, warnings


def parse_filename(filename: str, profile: Any) -> dict[str, Any]:
    """Parse `filename` against `profile` and return a parsed-fields map.

    Returns a dict ``{"parsed": ..., "unrecognized": ..., "warnings": ...}``.
    Never raises on malformed input: tokens that don't match any segment
    definition show up in `unrecognized`.
    """
    segments: list[dict[str, Any]] = list(profile.segments_json or [])
    delimiter: str = profile.delimiter
    inner_sep = _inner_separator(delimiter)

    name = _strip_extension(filename) if getattr(profile, "strip_extension", True) else filename
    raw_tokens = name.split(delimiter) if delimiter else [name]

    # Build identifier lookup keyed on casefolded identifier.
    by_identifier: dict[str, dict[str, Any]] = {}
    for seg in segments:
        ident = seg.get("identifier")
        if ident:
            by_identifier[ident.casefold()] = seg

    date_segment = _find_date_segment(segments)

    parsed: dict[str, str] = {}
    unrecognized: list[str] = []
    warnings: list[str] = []

    # Handle the YYYY-MM-DD-collides-with-`-`-delimiter case up front: the
    # delimiter-split shatters the date, so recombine triples first.
    tokens_to_classify = raw_tokens
    if (
        delimiter == "-"
        and date_segment is not None
        and date_segment.get("date_format") == "YYYY-MM-DD"
    ):
        tokens_to_classify, date_hits, recomb_warnings = _recombine_yyyy_mm_dd(raw_tokens)
        warnings.extend(recomb_warnings)
        if date_hits:
            parsed[date_segment["field_name"]] = date_hits[0][1]

    for token in tokens_to_classify:
        if not token:
            continue

        # Date-shaped (no identifier).
        if token[0].isdigit():
            if date_segment is not None:
                iso = _try_classify_date(token, date_segment.get("date_format"))
                if iso is not None and date_segment["field_name"] not in parsed:
                    parsed[date_segment["field_name"]] = iso
                    continue
            unrecognized.append(token)
            continue

        # Number-shaped: <letters><digits>.
        m = _NUMBER_TOKEN_RE.match(token)
        if m:
            ident, value = m.group(1), m.group(2)
            seg = by_identifier.get(ident.casefold())
            if seg is not None and seg.get("field_type") == "number":
                parsed[seg["field_name"]] = value
                continue
            unrecognized.append(token)
            continue

        # String-shaped: <letters><inner_sep><value>.
        if inner_sep in token:
            head, _, tail = token.partition(inner_sep)
            if head and head.isascii() and head.isalpha() and 1 <= len(head) <= 4:
                seg = by_identifier.get(head.casefold())
                if seg is not None and seg.get("field_type") == "string":
                    parsed[seg["field_name"]] = tail
                    continue
            unrecognized.append(token)
            continue

        # Pure letters, no digits, no inner sep: identifier alone is unrecognized.
        unrecognized.append(token)

    return {"parsed": parsed, "unrecognized": unrecognized, "warnings": warnings}


# ---------------------------------------------------------------------------
# Legacy stubs kept for import compatibility while auto-ingest is gated off.
#
# The gated body of process_ingest_event / process_manifest_ingest still
# references these names. The auto-ingest gate fires before either is called,
# so these stubs should never actually execute. The follow-up auto-ingest
# rework will delete both the stubs and the call sites. See
# local/Naming Profiles/spec-auto-ingest-neutralize.md.
# ---------------------------------------------------------------------------


def match_filename(*_args, **_kwargs):
    raise NotImplementedError(
        "match_filename was removed in the Naming Profile redesign. "
        "Auto-ingest is gated off; this name exists only so the gated "
        "module loads. See local/Naming Profiles/spec-auto-ingest-neutralize.md."
    )


async def resolve_entities(*_args, **_kwargs):
    raise NotImplementedError(
        "resolve_entities was removed in the Naming Profile redesign. "
        "Auto-ingest is gated off; this name exists only so the gated "
        "module loads. See local/Naming Profiles/spec-auto-ingest-neutralize.md."
    )
