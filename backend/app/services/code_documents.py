"""plan_8_5 section 3.5: the executable code inside a document, kept as what it is.

A word-processed supplement can hold a paper's entire analysis. Groff's holds five R Markdown
documents and a hundred chunks. Recording it as unreadable meant a paper that supplied all of its
code scored nothing for any code obligation, which is a statement about bioAF and was read as one
about the paper.

Extracting it is not licence to treat the document as one program:

- a **chunk** is the unit. It keeps its place in the document, its order, its label and its options,
  so a diagnostic can say where it is rather than pointing at a line of a file nobody supplied;
- **prose is not source.** The text around the chunks, and the YAML front matter, are not code and
  are not handed to a parser;
- a chunk that never closes is **recorded as unterminated**, not silently extended to the end.

Pure: it is given text and returns records.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*```+\s*\{\s*([A-Za-z][A-Za-z0-9_.]*)\s*([^}]*)\}\s*$")
_CLOSE = re.compile(r"^\s*```+\s*$")
_FRONT_MATTER = re.compile(r"^---\s*$")
_TITLE = re.compile(r"^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$")


def _new_document(title: str | None, line: int) -> dict:
    return {"title": title, "line": line, "chunks": []}


def rmarkdown_documents(text: str) -> list[dict]:
    """The R Markdown documents in this text, each with its chunks in order.

    ``[{"title", "line", "chunks": [{"engine", "label", "options", "code", "line", "order",
    "unterminated"}]}]``. A text with no front matter is one untitled document.
    """
    lines = (text or "").splitlines()
    documents: list[dict] = []
    current = _new_document(None, 1)
    chunk: dict | None = None
    body: list[str] = []
    in_front_matter = False
    order = 0
    for number, line in enumerate(lines, start=1):
        if chunk is not None:
            if _CLOSE.match(line):
                chunk["code"] = "\n".join(body).strip("\n")
                current["chunks"].append(chunk)
                chunk, body = None, []
                continue
            body.append(line)
            continue
        if _FRONT_MATTER.match(line):
            if in_front_matter:
                in_front_matter = False
                continue
            # A second front matter block starts a new document, and the first is kept as it stands.
            if current["chunks"] or current["title"]:
                documents.append(current)
                current = _new_document(None, number)
            in_front_matter = True
            continue
        if in_front_matter:
            match = _TITLE.match(line)
            if match and current["title"] is None:
                current["title"] = match.group(1).strip() or None
            continue
        fence = _FENCE.match(line)
        if fence:
            order += 1
            label, options = _chunk_header(fence.group(2))
            chunk = {
                "engine": fence.group(1).strip().lower(),
                "label": label,
                "options": options,
                "code": "",
                "line": number,
                "order": order,
                "unterminated": False,
            }
            body = []
    if chunk is not None:
        chunk["code"] = "\n".join(body).strip("\n")
        chunk["unterminated"] = True
        current["chunks"].append(chunk)
    documents.append(current)
    return documents


def _chunk_header(rest: str) -> tuple[str | None, str]:
    """A chunk header's label and the options beside it. ``{r setup, echo=FALSE}`` is ('setup', 'echo=FALSE')."""
    text = rest.strip()
    if not text:
        return None, ""
    first, _, remainder = text.partition(",")
    first = first.strip()
    if first and "=" not in first:
        return first, remainder.strip()
    return None, text


def has_rmarkdown_chunks(text: str) -> bool:
    """Whether this text holds at least one fenced chunk of executable code."""
    return any(_FENCE.match(line) for line in (text or "").splitlines())
