"""plan_8_3 section 1.1: open a supplementary archive, under bounded limits, and record what is in it.

Study 50's report stated that the authors published no result table, while its own completion facts
recorded three attachments that were never inspected. One of them is the paper's supplementary
archive. The decoder refused it correctly, as "an archive holding N files, and bioAF reads one table
at a time", and nothing then opened it. A downloaded archive is not proof that its contents were
inspected, and an uninspected archive is never evidence of what a paper did not publish.

**Bounded, and bounded before anything is decompressed.** The member count and the declared expanded
size are read from the archive's own directory, so a bomb is refused without being read. A member
over the per-member limit is NAMED and left unread rather than silently dropped. A member whose path
escapes the archive refuses the whole thing: a supplement is not a place to write files from.

**One level.** A nested archive is named as one and not opened. Deeper nesting is neither refused nor
pretended to be inspected; it is on the record as what it is.
"""

from __future__ import annotations

import io
import logging
import zipfile

logger = logging.getLogger("bioaf.supplement_archives")

ARCHIVE_VERSION = 1

# How much an archive may expand to, in total, and per member. A publisher's supplementary bundle is
# a handful of tables and figures; anything on another scale is refused and said so, never truncated
# into a partial inspection that would read as a complete one.
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 200

EXPANDED = "expanded"
REFUSED = "refused"

# Why an archive was refused. Each sends whoever reads the report somewhere different.
REASON_MEMBER_COUNT = "member_count"
REASON_EXPANDED_SIZE = "expanded_size"
REASON_UNSAFE_PATH = "unsafe_path"
REASON_CORRUPT = "corrupt"
REASON_KINDS = (REASON_MEMBER_COUNT, REASON_EXPANDED_SIZE, REASON_UNSAFE_PATH, REASON_CORRUPT)

# What a publisher's packaging tool leaves behind, which is not part of what the authors published.
_PUBLISHER_NOISE = ("__MACOSX/", ".DS_Store", "Thumbs.db")
_ARCHIVE_SUFFIXES = (".zip",)


def _members_of(raw: bytes) -> list[zipfile.ZipInfo] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return [info for info in archive.infolist() if not info.is_dir()]
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError):
        return None


def _noise(name: str) -> bool:
    return any(part in name for part in _PUBLISHER_NOISE) or name.rsplit("/", 1)[-1].startswith("._")


def is_archive(raw: bytes, filename: str = "") -> bool:
    """Whether these bytes are an archive of several files bioAF should open.

    A workbook is a zip on disk and ONE table to bioAF, which the shared decoder already reads; it is
    not an archive to expand, and expanding it would turn one supplement into a dozen XML fragments.
    """
    if not isinstance(raw, (bytes, bytearray)) or raw[:2] != b"PK":
        return False
    members = _members_of(bytes(raw))
    if members is None:
        # Bytes that begin PK and do not open are a corrupt archive, which this module reports.
        return str(filename or "").lower().endswith(_ARCHIVE_SUFFIXES)
    names = [m.filename for m in members]
    if any(name.startswith("xl/") or name.startswith("word/") or name.startswith("ppt/") for name in names):
        return False
    return len([n for n in names if not _noise(n)]) > 1 or str(filename or "").lower().endswith(_ARCHIVE_SUFFIXES)


def _unsafe(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\") or ":" in name.split("/")[0][1:2]:
        return True
    return any(part == ".." for part in name.replace("\\", "/").split("/"))


def _refused(kind: str, reason: str, filename: str) -> dict:
    return {
        "status": REFUSED,
        "archive": filename,
        "archive_version": ARCHIVE_VERSION,
        "members": [],
        "reason_kind": kind,
        "reason": reason,
    }


def expand_archive(
    raw: bytes,
    filename: str,
    *,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_members: int = MAX_MEMBERS,
) -> dict:
    """The archive's members, each with its bytes and where it came from. Never raises.

    ``{"status": "expanded" | "refused", "members": [...], "reason_kind", "reason"}``. A refusal names
    which limit refused it, because "this archive is too large" and "this archive is corrupt" send a
    reader to different places.
    """
    members = _members_of(bytes(raw or b""))
    if members is None:
        return _refused(REASON_CORRUPT, f"{filename} could not be opened as an archive", filename)
    real = [m for m in members if not _noise(m.filename)]
    unsafe = [m.filename for m in real if _unsafe(m.filename)]
    if unsafe:
        return _refused(
            REASON_UNSAFE_PATH,
            f"{filename} holds a file whose path leads outside it ({unsafe[0]}), so bioAF did not open it",
            filename,
        )
    if len(real) > max_members:
        return _refused(
            REASON_MEMBER_COUNT,
            f"{filename} holds {len(real)} files, and bioAF opens at most {max_members} from one archive",
            filename,
        )
    declared = sum(int(m.file_size or 0) for m in real)
    if declared > max_expanded_bytes:
        # Read from the archive's own directory: nothing has been decompressed at this point.
        return _refused(
            REASON_EXPANDED_SIZE,
            f"{filename} says it expands to {declared:,} bytes, over bioAF's limit of {max_expanded_bytes:,}",
            filename,
        )

    rows: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(raw))) as archive:
            for info in real:
                nested = is_archive_name(info.filename)
                over = int(info.file_size or 0) > max_member_bytes
                blob = None
                if not nested and not over:
                    with archive.open(info) as handle:
                        blob = handle.read(max_member_bytes + 1)
                        if len(blob) > max_member_bytes:
                            blob, over = None, True
                rows.append(
                    {
                        "name": info.filename,
                        "bytes": blob,
                        "size_bytes": int(info.file_size or 0),
                        "contained_in": filename,
                        "archive_version": ARCHIVE_VERSION,
                        "nested_archive": nested,
                        "over_limit": over,
                    }
                )
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError) as exc:
        logger.info("supplementary archive %s could not be read: %s", filename, exc)
        return _refused(REASON_CORRUPT, f"{filename} could not be read: {exc}", filename)

    return {
        "status": EXPANDED,
        "archive": filename,
        "archive_version": ARCHIVE_VERSION,
        "members": rows,
        "reason_kind": None,
        "reason": None,
    }


def is_archive_name(name: str) -> bool:
    """Whether a member's own name says it is another archive. Nesting is named, never opened."""
    return str(name or "").lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".7z", ".rar"))
