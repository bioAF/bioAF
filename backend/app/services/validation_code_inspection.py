"""plan_8_4 section 6.2: turn the bytes a study already holds into source a check can read.

**Discovery is not inspection.** A repository that exists, a supplement that was retrieved and a role
that says "code" are three facts about a file; none of them is source text. This step is what makes
the difference, and it is deliberately narrow:

- a file whose bytes decode as text becomes ONE source, under its own path, with its own checksum, so
  a check assesses what was supplied rather than a concatenation of everything;
- a dependency or environment specification is kept apart from the source, because they answer
  different obligations;
- a format bioAF cannot split back into files is recorded as unreadable with the reason. Groff's code
  is a word-processed document, and inventing file boundaries for it would put a file in the record
  that the authors never supplied.

Pure: it is given bytes and returns a record. No network, no model, no execution.
"""

from __future__ import annotations

import hashlib
import pathlib

CODE_ROLE = "code"
MANIFEST_ROLE = "supporting_input"

# Extension to the language a parser would be asked for. A language absent from
# `validation_code_checks.SUPPORTED_LANGUAGES` is still recorded: what bioAF cannot do is PARSE it,
# and the checks say that for themselves rather than this step pretending the file does not exist.
LANGUAGES = {
    ".py": "python",
    ".r": "r",
    ".rmd": "r",
    ".sh": "shell",
    ".jl": "julia",
    ".pl": "perl",
    ".m": "matlab",
    ".nf": "nextflow",
    ".smk": "snakemake",
}

# Dependency and environment specifications, which answer C2 and C3 rather than C1.
MANIFESTS = {
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "pyproject.toml",
    "setup.py",
    "poetry.lock",
    "conda.yml",
    "dockerfile",
    "renv.lock",
    "description",
    "makefile",
}

# Documents and archives: real containers of code. plan_8_5 section 3.5: a .docx whose text is R
# Markdown is read back into its chunks, which are units the authors wrote; anything else stays a
# container bioAF cannot split, because inventing file boundaries would put files in the record that
# were never supplied.
_OPAQUE = (".docx", ".doc", ".pdf", ".rtf", ".odt")
_DOCUMENTS = (".docx",)


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _text(blob: bytes) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            decoded = blob.decode(encoding)
        except (UnicodeDecodeError, AttributeError):
            continue
        if "\x00" in decoded:
            return None
        return decoded
    return None


def _is_manifest(name: str) -> bool:
    base = pathlib.PurePosixPath(name).name.lower()
    return base in MANIFESTS or base.startswith("dockerfile")


def inspect_code(supplements: list[dict] | None, *, bytes_for: dict[str, bytes] | None = None) -> dict:
    """``{"sources", "manifests", "unreadable"}`` from the files this study holds.

    ``bytes_for`` maps a supplement's filename to the bytes in hand. A file with no bytes is recorded
    as unreadable rather than skipped: "bioAF never read it" and "bioAF read it and found nothing" are
    different statements about a paper.
    """
    bytes_for = bytes_for or {}
    sources: list[dict] = []
    manifests: list[dict] = []
    unreadable: list[dict] = []
    for row in supplements or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("filename") or "")
        role = row.get("role")
        manifest = _is_manifest(name)
        if role != CODE_ROLE and not manifest:
            continue
        blob = bytes_for.get(name)
        if blob is None:
            unreadable.append({"path": name, "reason": "bioAF holds no bytes for this file"})
            continue
        if name.lower().endswith(_DOCUMENTS):
            extracted = _documented_sources(name, blob)
            if extracted:
                sources.extend(extracted)
                continue
        if name.lower().endswith(_OPAQUE):
            unreadable.append(
                {
                    "path": name,
                    "reason": (
                        "the code is inside a word-processed document, and bioAF does not recover the "
                        "file boundaries of the scripts it contains; inventing them would put files in "
                        "the record that were never supplied"
                    ),
                    "sha256": _sha256(blob),
                }
            )
            continue
        text = _text(blob)
        if text is None:
            unreadable.append({"path": name, "reason": "the bytes do not decode as text", "sha256": _sha256(blob)})
            continue
        entry = {
            "path": name,
            "text": text,
            "provenance": {"from": "supplement", "sha256": _sha256(blob), "bytes": len(blob)},
        }
        if manifest:
            manifests.append(entry)
            continue
        suffix = pathlib.PurePosixPath(name).suffix.lower()
        sources.append({**entry, "language": LANGUAGES.get(suffix, "unknown")})
    return {"sources": sources, "manifests": manifests, "unreadable": unreadable}


# The chunk engines bioAF reads back into source, and the language each one is.
_ENGINES = {"r": "r", "python": "python", "bash": "shell", "sh": "shell"}


def _documented_sources(name: str, blob: bytes) -> list[dict]:
    """The R Markdown documents inside a word-processed file, as sources with their chunks.

    plan_8_5 section 3.5: Groff's supplement is five R Markdown documents and a hundred chunks inside
    one .docx. Recording it as unreadable left every code obligation grey for a paper that supplied
    all of its code. One source per DOCUMENT the authors wrote, its chunks kept in order with their
    labels and their places, and the prose between them left out, because prose is not source.
    """
    from app.services.code_documents import has_rmarkdown_chunks, rmarkdown_documents
    from app.services.supplement_inventory import extract_docx_text

    text = extract_docx_text(blob)
    if not text or not has_rmarkdown_chunks(text):
        return []
    found: list[dict] = []
    documents = rmarkdown_documents(text)
    titles = [d["title"] or f"document {i}" for i, d in enumerate(documents, start=1)]
    for index, document in enumerate(documents, start=1):
        by_language: dict[str, list[dict]] = {}
        for chunk in document["chunks"]:
            language = _ENGINES.get(chunk["engine"])
            if language is None or not (chunk["code"] or "").strip():
                continue
            by_language.setdefault(language, []).append(
                {
                    "id": f"chunk {chunk['order']}" + (f" ({chunk['label']})" if chunk["label"] else ""),
                    "label": chunk["label"] or f"chunk {chunk['order']}",
                    "line": chunk["line"],
                    "order": chunk["order"],
                    "code": chunk["code"],
                    "unterminated": chunk["unterminated"],
                }
            )
        title = document["title"] or f"document {index}"
        if titles.count(title) > 1:
            # Two of Groff's five documents are both titled "R Notebook". They are still two
            # documents, and a diagnostic has to say which one it is about.
            title = f"{title} {1 + titles[:index - 1].count(title)}"
        for language, segments in by_language.items():
            found.append(
                {
                    "path": f"{name} > {title}",
                    "language": language,
                    "text": "\n".join(s["code"] for s in segments),
                    "segments": segments,
                    "document": title,
                    "provenance": {
                        "from": "supplement",
                        "sha256": _sha256(blob),
                        "bytes": len(blob),
                        "extracted": "r markdown in a word-processed document",
                        "chunks": len(segments),
                        "container": name,
                    },
                }
            )
    return found


def is_code_file(filename: str, *, role: str | None = None) -> bool:
    """Whether this file is the paper's code, or a specification of the environment that runs it.

    Used where the bytes are in hand, to decide what is worth keeping. A results table is not code
    however it is classified, and a role of ``code`` is taken at its word: it was decided by reading
    the file, not by looking at its name.
    """
    return role == CODE_ROLE or _is_manifest(filename or "")
