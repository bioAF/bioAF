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
    # plan_8_6 section 4: a notebook is source. Study 65 publishes its single-cell analysis as one,
    # and with no entry here it was a file with no text however many times its bytes arrived.
    ".ipynb": "notebook",
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


# plan_8_6 section 4: an environment specification the authors named for themselves. Study 65's is
# `flow_env.yml`, which is a conda environment under a name no fixed list would hold. The suffix is
# what says so, and a YAML that is not named as an environment is not read as one.
_ENVIRONMENT_NAMES = ("env.yml", "env.yaml", "environment.yml", "environment.yaml")


def _is_manifest(name: str) -> bool:
    base = pathlib.PurePosixPath(name).name.lower()
    return base in MANIFESTS or base.startswith("dockerfile") or base.endswith(_ENVIRONMENT_NAMES)


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
        if name.lower().endswith(".ipynb"):
            # plan_8_6 section 4: a notebook is source, read as JSON and never executed.
            notebook = _notebook_source(
                name, blob, provenance={"from": "supplement", "sha256": _sha256(blob), "bytes": len(blob)}
            )
            if notebook is not None:
                sources.append(notebook)
            else:
                unreadable.append(
                    {"path": name, "reason": "the notebook is not readable JSON", "sha256": _sha256(blob)}
                )
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
            title = f"{title} {1 + titles[: index - 1].count(title)}"
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


# A notebook magic is notebook syntax, not Python. `%matplotlib inline` and `!pip install` are the
# notebook's own language, and handing them to a Python parser would report a paper's code as
# syntactically broken when it is nothing of the kind.
_MAGIC = ("%", "!", "?")


def read_notebook(blob: bytes | str | None) -> dict | None:
    """A Jupyter notebook read as JSON: its language, its code cells in order, and where each sits.

    plan_8_6 section 4. ``{"language", "kernel", "cells", "text", "markdown", "magics"}``, or None
    when this is not a notebook. Markdown and saved outputs are kept apart from source: prose is not
    code, and an output is not the step that produced it. Nothing is executed and nothing imported;
    this is `json.loads` and a walk over what it returns.
    """
    import json

    try:
        raw = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else str(blob or "")
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("cells"), list):
        return None

    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    kernel = metadata.get("kernelspec") if isinstance(metadata.get("kernelspec"), dict) else {}
    language_info = metadata.get("language_info") if isinstance(metadata.get("language_info"), dict) else {}
    language = str(kernel.get("language") or language_info.get("name") or "").strip().lower() or "unknown"

    cells: list[dict] = []
    markdown: list[str] = []
    magics: list[dict] = []
    line = 1
    for order, cell in enumerate(document["cells"], start=1):
        if not isinstance(cell, dict):
            continue
        source = cell.get("source")
        body = "".join(source) if isinstance(source, list) else str(source or "")
        if cell.get("cell_type") != "code":
            if body.strip():
                markdown.append(body)
            continue
        kept: list[str] = []
        for offset, text in enumerate(body.splitlines()):
            if text.lstrip().startswith(_MAGIC) and not text.lstrip().startswith("#"):
                magics.append({"cell": order, "line": line + offset, "text": text.strip()})
                continue
            kept.append(text)
        code = "\n".join(kept).strip("\n")
        if not code.strip():
            line += body.count("\n") + 1
            continue
        cells.append(
            {
                "id": f"cell {order}" + (f" ({cell['id']})" if isinstance(cell.get("id"), str) else ""),
                "label": f"cell {order}",
                "order": order,
                "line": line,
                "code": code,
            }
        )
        line += body.count("\n") + 1
    return {
        "language": language,
        "kernel": kernel or language_info,
        "cells": cells,
        "markdown": markdown,
        "magics": magics,
        "text": "\n".join(c["code"] for c in cells),
    }


def _notebook_source(name: str, blob: bytes, *, provenance: dict) -> dict | None:
    """One notebook as one source, its cells kept as the segments a citation can resolve to."""
    found = read_notebook(blob)
    if found is None:
        return None
    return {
        "path": name,
        "language": found["language"] if found["language"] in ("python", "r", "julia") else "unknown",
        "text": found["text"],
        "segments": [
            {"id": c["id"], "label": c["label"], "line": c["line"], "order": c["order"], "code": c["code"]}
            for c in found["cells"]
        ],
        "notebook": {"kernel": found["kernel"], "cells": len(found["cells"]), "magics": found["magics"]},
        "provenance": {**provenance, "extracted": "code cells of a Jupyter notebook"},
    }


# plan_8_6 section 4: a repository is source code, and a member in the hundreds of megabytes is a
# data drop. What the cap bounds is the SOURCE bioAF keeps, not the file it came out of.
#
# The owner, 2026-09-21: study 65's `scRNA/scanpy_analysis.ipynb` is 3.6 MB and was skipped by a
# 2 MB file-size cap WITHOUT A WORD. Its actual code is about 9 KB; the bulk is saved outputs, which
# are not source and never reach an assessor. M1.B then deducted two points for the very parameters
# that notebook states, and nothing on the record could tell incomplete inspection from missing
# author documentation. A notebook is read up to a container cap and kept only for its code; a
# member bioAF does not read is RECORDED as unread, never silently dropped.
MAX_SOURCE_BYTES = 2 * 1024 * 1024
# What a notebook's container may weigh before bioAF stops opening it. Outputs dominate the file and
# are discarded, so this bounds the read rather than the source.
MAX_NOTEBOOK_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 400


def _cap_for(name: str) -> int:
    """How large a member may be before bioAF stops opening it.

    A notebook is a container: its saved outputs are the bulk of the file and are discarded, so the
    cap bounds the READ rather than the source. Everything else is capped at the source it would
    become.
    """
    return MAX_NOTEBOOK_BYTES if str(name or "").lower().endswith(".ipynb") else MAX_SOURCE_BYTES


def inspect_archive(blob: bytes | None, *, origin: str | None = None) -> dict:
    """``{"sources", "manifests", "unreadable"}`` from the bytes of a fetched repository or archive.

    plan_8_6 section 4: fetching a list of filenames does not complete inspection. This reads the
    members bioAF fetched into source a check can parse, under their own paths, each with its own
    checksum, keeping dependency and environment specifications apart from the scripts.

    Never raises and never executes: an archive bioAF cannot open is recorded as unreadable.
    """
    import io
    import tarfile
    import zipfile

    sources: list[dict] = []
    manifests: list[dict] = []
    unreadable: list[dict] = []
    skipped: list[dict] = []
    oversized: list[tuple[str, int]] = []
    if not blob:
        return {
            "sources": [],
            "manifests": [],
            "unreadable": [{"path": origin or "", "reason": "no bytes were held"}],
            "skipped": [],
        }

    members: list[tuple[str, bytes]] = []
    try:
        if bytes(blob[:2]) == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > _cap_for(info.filename):
                        oversized.append((info.filename, info.file_size))
                        continue
                    members.append((info.filename, archive.read(info.filename)))
        else:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    if member.size > _cap_for(member.name):
                        # Named and honestly not read. Silently skipping it is what let an assessor
                        # read bioAF's incomplete inspection as the authors' missing documentation.
                        oversized.append((member.name, member.size))
                        continue
                    handle = archive.extractfile(member)
                    if handle is not None:
                        members.append((member.name, handle.read()))
    except Exception as exc:  # noqa: BLE001 - an archive bioAF refused is on the record as refused
        return {
            "sources": [],
            "manifests": [],
            "unreadable": [{"path": origin or "", "reason": f"the archive could not be read ({exc})"}],
            "skipped": [],
        }

    # A repository tarball wraps everything in `<repo>-<sha>/`. Stripping it keeps a cited path the
    # one a reader would type.
    named = [name for name, _ in members] + [name for name, _ in oversized]
    prefixes = {name.split("/", 1)[0] for name in named if "/" in name}
    strip = prefixes.pop() + "/" if len(prefixes) == 1 and all("/" in name for name in named) else ""

    for name, size in oversized:
        safe = str(name).removeprefix(strip)
        reason = (
            f"this file is {size} bytes, larger than the {_cap_for(name)}-byte cap for a member bioAF "
            "opens, so bioAF did not read it and what it contains is not established"
        )
        # Every member bioAF declined is on the record. A code file or a manifest is also
        # `unreadable`, because that is the list the code checks read, and an unrecorded skip is
        # what let an assessor read bioAF's incomplete inspection as the authors' missing
        # documentation.
        skipped.append({"path": safe, "reason": reason, "size_bytes": size})
        if _is_manifest(safe) or pathlib.PurePosixPath(safe).suffix.lower() in LANGUAGES:
            unreadable.append({"path": safe, "reason": reason, "size_bytes": size})

    for name, member_bytes in members[:MAX_SOURCES]:
        safe = str(name).removeprefix(strip)
        if not safe or safe.endswith("/"):
            continue
        manifest = _is_manifest(safe)
        suffix = pathlib.PurePosixPath(safe).suffix.lower()
        if not manifest and suffix not in LANGUAGES:
            continue
        provenance = {
            "from": "repository",
            "origin": origin,
            "sha256": _sha256(member_bytes),
            "bytes": len(member_bytes),
        }
        if suffix == ".ipynb":
            notebook = _notebook_source(safe, member_bytes, provenance=provenance)
            if notebook is not None:
                sources.append(notebook)
            else:
                unreadable.append(
                    {"path": safe, "reason": "the notebook is not readable JSON", "sha256": provenance["sha256"]}
                )
            continue
        text = _text(member_bytes)
        if text is None:
            unreadable.append(
                {"path": safe, "reason": "the bytes do not decode as text", "sha256": provenance["sha256"]}
            )
            continue
        entry = {"path": safe, "text": text, "provenance": provenance}
        if manifest:
            manifests.append(entry)
        else:
            sources.append({**entry, "language": LANGUAGES.get(suffix, "unknown")})
    return {"sources": sources, "manifests": manifests, "unreadable": unreadable, "skipped": skipped}


def is_code_file(filename: str, *, role: str | None = None) -> bool:
    """Whether this file is the paper's code, or a specification of the environment that runs it.

    Used where the bytes are in hand, to decide what is worth keeping. A results table is not code
    however it is classified, and a role of ``code`` is taken at its word: it was decided by reading
    the file, not by looking at its name.
    """
    return role == CODE_ROLE or _is_manifest(filename or "")
