"""Integrity mechanics shared by the database backups: the upgrade recovery backup (app/schema_upgrade.py) and the
Postgres backups in the backups bucket (backup_service).

- ``file_sha256`` hashes a file in blocks (a dump can be larger than the backend's memory limit); callers run it
  off the event loop. ``tables_with_data`` reads a ``pg_restore --list`` table of contents. Each caller keeps its own
  process lifecycle and its own completeness rule.
- **Marker.** A Postgres backup made by this code is stored with object metadata naming the format, the backup's own
  name and the SHA-256 computed at creation (``marker``). ``classify`` reads it back: ``checksummed`` with the
  expected digest, ``unverified`` for a backup the cutover inventory names as older than markers, ``invalid`` for
  anything else (a partial or foreign marker, an unknown format, an unmarked object made after the cutover).
- **Cutover.** ``postgres/.integrity-cutover.json`` lists the backups that existed before the first marked backup.
  Only they qualify for the explicit unverified-restore opt-in.

The digest checks the stored bytes against what this installation recorded when it made the backup. It is not a
signature: a writer able to replace both an object and its metadata is outside what it detects.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path

FORMAT = "1"
KEY_FORMAT = "bioaf-backup-format"
KEY_ID = "bioaf-backup-id"
KEY_SHA256 = "bioaf-sha256"
CUTOVER_KEY = "postgres/.integrity-cutover.json"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class IntegrityError(Exception):
    """A marker, digest or inventory does not hold; the backup is not trusted."""


def file_sha256(path: Path) -> str:
    """SHA-256 of a file read in blocks: a dump can be larger than the backend's memory limit."""
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def tables_with_data(listing: str) -> set[str]:
    """Tables a ``pg_restore --list`` listing has data entries for (comment lines skipped)."""
    return {line.split()[-2] for line in listing.splitlines() if " TABLE DATA " in line and not line.startswith(";")}


def marker(backup_id: str, sha256: str) -> dict[str, str]:
    if not _DIGEST.match(sha256):
        raise IntegrityError("a backup digest is 64 lowercase hex characters")
    return {KEY_FORMAT: FORMAT, KEY_ID: backup_id, KEY_SHA256: sha256}


def classify(name: str, metadata: dict[str, str], *, legacy: frozenset[str]) -> tuple[str, str | None]:
    """``(state, expected sha256)`` for a stored backup; see the module docstring."""
    ours = {k: v for k, v in metadata.items() if k.startswith("bioaf-")}
    if not ours:
        return ("unverified", None) if name in legacy else ("invalid", None)
    if (
        set(ours) == {KEY_FORMAT, KEY_ID, KEY_SHA256}
        and ours[KEY_FORMAT] == FORMAT
        and ours[KEY_ID] == name
        and _DIGEST.match(ours[KEY_SHA256])
    ):
        return "checksummed", ours[KEY_SHA256]
    return "invalid", None


def cutover_document(legacy_names: Iterable[str], *, created_at: str) -> bytes:
    return json.dumps({"format": 1, "created_at": created_at, "legacy": sorted(set(legacy_names))}, indent=1).encode()


def parse_cutover(data: bytes) -> frozenset[str]:
    try:
        doc = json.loads(data)
    except ValueError as exc:
        raise IntegrityError("the backup cutover record is not JSON") from exc
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("legacy"), list):
        raise IntegrityError("the backup cutover record is not a version 1 inventory")
    if not all(isinstance(n, str) for n in doc["legacy"]):
        raise IntegrityError("the backup cutover record lists a non-name")
    return frozenset(doc["legacy"])
