"""Archive member names for data exports.

The layout (owner-accepted, plan 24 decision 1):

- An experiment export's root is ``experiment_<uuid>_<name>/``; its data files are at
  ``{results,raw}/<file-uuid>/<basename>`` under it, beside ``README.txt``, ``sample_manifest.csv``
  and ``provenance/``.
- A project export's root is ``project_<uuid>_<name>/``. Experiment roots nest under its
  ``experiments/``; files that belong to the project but to none of its experiments use the same
  ``{results,raw}/<file-uuid>/<basename>`` layout at the project root.

Catalogue UUIDs make every root and data member unique, so neither two files with one basename nor
two experiments whose names sanitize alike can collide. Names are readable, not identities: every
component is sanitized and bounded, and ``join`` refuses anything that could escape the archive
root or exceed the bounds the scratch reservation's ZIP overhead allowance assumes.
"""

from __future__ import annotations

import unicodedata
import uuid

MAX_COMPONENT_BYTES = 128
MAX_MEMBER_BYTES = 512
# An extension longer than this is not worth keeping over the readable stem.
_MAX_EXTENSION_BYTES = 32
_ALLOWED_PUNCTUATION = "-_."


class UnsafeMemberName(ValueError):
    """An archive member name that is unsafe, over its bound, or already taken."""


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _split_extension(name: str) -> tuple[str, str]:
    """``("reads", ".fastq.gz")``: the compound extension when short enough, else the last one."""
    if "." not in name:
        return name, ""
    stem, _, rest = name.partition(".")
    if stem and len(rest.encode("utf-8")) < _MAX_EXTENSION_BYTES:
        return stem, "." + rest
    stem, _, last = name.rpartition(".")
    if stem and len(last.encode("utf-8")) < _MAX_EXTENSION_BYTES:
        return stem, "." + last
    return name, ""


def safe_component(text: str, *, fallback: str, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    """One readable path component: letters, digits and ``-_.`` only, never empty or dot-only.

    Separators, controls, quotes, colons and spaces become ``_``; leading and trailing ``_`` and
    ``.`` are dropped. A name too long for ``max_bytes`` keeps its extension and loses stem text.
    """
    text = unicodedata.normalize("NFC", text or "")
    cleaned = "".join(c if c.isalnum() or c in _ALLOWED_PUNCTUATION else "_" for c in text).strip("_.")
    if not cleaned:
        return fallback
    if len(cleaned.encode("utf-8")) <= max_bytes:
        return cleaned
    stem, extension = _split_extension(cleaned)
    stem = _truncate_utf8(stem, max_bytes - len(extension.encode("utf-8"))).rstrip("_.")
    return (stem + extension) if stem else _truncate_utf8(cleaned, max_bytes)


def _root(kind: str, identity: uuid.UUID, name: str) -> str:
    prefix = f"{kind}_{identity}"
    readable = safe_component(name, fallback="", max_bytes=MAX_COMPONENT_BYTES - len(prefix) - 1)
    return f"{prefix}_{readable}" if readable else prefix


def experiment_root(identity: uuid.UUID, name: str) -> str:
    return _root("experiment", identity, name)


def project_root(identity: uuid.UUID, name: str) -> str:
    return _root("project", identity, name)


def data_member(folder: str, file_uuid: uuid.UUID, filename: str) -> str:
    """``<folder>/<file-uuid>/<basename>``: unique per catalogue file whatever its basename."""
    return join(folder, str(file_uuid), safe_component(filename, fallback="file"))


def _check_component(component: str) -> None:
    if component in ("", ".", ".."):
        raise UnsafeMemberName("An archive path component is empty or a dot name")
    if any(c in component for c in "/\\:") or any(unicodedata.category(c) == "Cc" for c in component):
        raise UnsafeMemberName("An archive path component holds a separator, drive marker or control")
    if len(component.encode("utf-8")) > MAX_COMPONENT_BYTES:
        raise UnsafeMemberName(f"An archive path component exceeds {MAX_COMPONENT_BYTES} bytes")


def join(*components: str) -> str:
    """Join already-sanitized components, refusing an unsafe one or an over-long result."""
    for component in components:
        _check_component(component)
    member = "/".join(components)
    if len(member.encode("utf-8")) > MAX_MEMBER_BYTES:
        raise UnsafeMemberName(f"An archive path exceeds {MAX_MEMBER_BYTES} bytes")
    return member


class MemberNames:
    """The members one archive has claimed; a second claim of any name (in any case) is refused.

    Case is folded because extraction on a case-insensitive filesystem would merge the two. A
    file is also refused where an earlier member already made the same name a directory, and the
    reverse, since an extractor cannot create both.
    """

    def __init__(self) -> None:
        self._files: set[str] = set()
        self._directories: set[str] = set()

    def claim(self, member: str) -> str:
        join(*member.split("/"))
        key = member.casefold()
        parents = key.split("/")[:-1]
        if key in self._files or key in self._directories:
            raise UnsafeMemberName("An archive member name is already taken")
        for depth in range(1, len(parents) + 1):
            if "/".join(parents[:depth]) in self._files:
                raise UnsafeMemberName("An archive member name is already taken")
        self._files.add(key)
        for depth in range(1, len(parents) + 1):
            self._directories.add("/".join(parents[:depth]))
        return member
