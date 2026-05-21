"""Paper identity helpers: title normalization and author-key derivation.

These helpers are pure (no DB) so the schema-level dedup constraints in
literature_papers can be populated deterministically before insert.
"""

from __future__ import annotations

import re
import unicodedata


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercase, strip diacritics, collapse all non-alphanumerics to whitespace,
    then collapse whitespace to single spaces. Empty titles return empty."""
    if not title:
        return ""
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    spaced = _NON_ALNUM.sub(" ", lowered)
    return _WHITESPACE.sub(" ", spaced).strip()


def author_key(author: dict | None) -> str:
    """LastnameFirstInitialMiddleInitial. Returns empty string when no usable data."""
    if not author:
        return ""
    family = (author.get("family") or "").strip()
    given = (author.get("given") or "").strip()
    if not family:
        return ""
    family_clean = re.sub(r"[^A-Za-z]", "", family)
    initials = ""
    for token in given.split():
        cleaned = re.sub(r"[^A-Za-z]", "", token)
        if cleaned:
            initials += cleaned[0].upper()
    return family_clean.capitalize() + initials


def first_and_last_author_keys(authors: list[dict] | None) -> tuple[str, str]:
    if not authors:
        return "", ""
    first = author_key(authors[0])
    last = author_key(authors[-1]) if len(authors) > 1 else first
    return first, last
