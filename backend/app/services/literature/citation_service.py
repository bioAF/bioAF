"""Citation export for Papers: BibTeX and RIS.

Both formats are generated deterministically from stored
literature_papers metadata. No external CrossRef fetch in v1.
"""

from __future__ import annotations

import re

from app.models.literature import LiteraturePaper

_BIBTEX_KEY_NON_ASCII = re.compile(r"[^A-Za-z0-9]+")


def _escape_bibtex(value: str) -> str:
    """Escape characters that are not safe in a BibTeX field value."""
    if value is None:
        return ""
    return (
        value.replace("\\", "\\\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def _bibtex_key(paper: LiteraturePaper) -> str:
    first_family = ""
    if paper.authors_json:
        first_family = (paper.authors_json[0].get("family") or "").strip()
    first_family_clean = _BIBTEX_KEY_NON_ASCII.sub("", first_family).lower() or "anon"
    year = paper.publication_date.year if paper.publication_date else "n.d."
    title = (paper.title or "untitled").strip().split()
    title_word = _BIBTEX_KEY_NON_ASCII.sub("", title[0]).lower() if title else "untitled"
    return f"{first_family_clean}{year}{title_word}"


def _authors_string(paper: LiteraturePaper) -> str:
    parts = []
    for a in paper.authors_json or []:
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if family and given:
            parts.append(f"{family}, {given}")
        elif family:
            parts.append(family)
        elif given:
            parts.append(given)
    return " and ".join(parts)


def to_bibtex(paper: LiteraturePaper) -> str:
    entry_type = "article" if paper.journal else "misc"
    key = _bibtex_key(paper)
    lines = [f"@{entry_type}{{{key},"]
    if paper.title:
        lines.append(f"  title = {{{_escape_bibtex(paper.title)}}},")
    authors = _authors_string(paper)
    if authors:
        lines.append(f"  author = {{{_escape_bibtex(authors)}}},")
    if paper.journal:
        lines.append(f"  journal = {{{_escape_bibtex(paper.journal)}}},")
    if paper.publication_date:
        lines.append(f"  year = {{{paper.publication_date.year}}},")
        lines.append(f"  month = {{{paper.publication_date.month:02d}}},")
    if paper.doi:
        lines.append(f"  doi = {{{paper.doi}}},")
    if paper.abstract:
        lines.append(f"  abstract = {{{_escape_bibtex(paper.abstract)}}},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines)


def to_ris(paper: LiteraturePaper) -> str:
    record_type = "JOUR" if paper.journal else "GEN"
    lines = [f"TY  - {record_type}"]
    if paper.title:
        lines.append(f"TI  - {paper.title}")
    for a in paper.authors_json or []:
        family = (a.get("family") or "").strip()
        given = (a.get("given") or "").strip()
        if family and given:
            lines.append(f"AU  - {family}, {given}")
        elif family:
            lines.append(f"AU  - {family}")
    if paper.journal:
        lines.append(f"JO  - {paper.journal}")
    if paper.publication_date:
        date_str = paper.publication_date.strftime("%Y/%m/%d")
        lines.append(f"PY  - {paper.publication_date.year}")
        lines.append(f"DA  - {date_str}")
    if paper.doi:
        lines.append(f"DO  - {paper.doi}")
    if paper.abstract:
        lines.append(f"AB  - {paper.abstract}")
    lines.append("ER  - ")
    return "\n".join(lines)


def bulk_export(papers: list[LiteraturePaper], fmt: str) -> str:
    if fmt == "bibtex":
        return "\n\n".join(to_bibtex(p) for p in papers)
    if fmt == "ris":
        return "\n\n".join(to_ris(p) for p in papers)
    raise ValueError(f"unsupported format: {fmt}")
