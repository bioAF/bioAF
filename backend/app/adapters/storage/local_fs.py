"""Objects as files: the filesystem object store shared by GCS/S3 local modes and NFS.

``<scheme>://<container>/<key>`` maps to ``<root>/<container>/<key>``. The GCS and S3 adapters use
it for their development/CI local mode (root ``$BIOAF_LOCAL_DATA_ROOT/_objects``); the NFS adapter
uses it for the mounted store itself (containers are its logical stores). Provider wrappers keep
their schemes, roots, capabilities, metadata additions and cloud dispatch; this owns only the
filesystem mechanics, so they are written once.

Every method is blocking: callers run it with ``asyncio.to_thread``. The contract (plan 17 findings,
delivery 4 matrix):

- One resolver confines every operation, listings included, to the root: the container is a single
  plain segment, the key has no absolute, ``.`` or ``..`` segment, and the real path (symlinks
  followed) must stay under the root. Direct operations on an unsafe URI raise ``ValidationError``;
  a listing skips unsafe entries so one bad link cannot hide the rest.
- A directory is not an object: ``exists`` is False, reads raise ``StorageObjectNotFound``, delete
  does nothing, listings skip it.
- Deleting a symlink removes the link, never its target.
- Listings match keys literally, sort by key, honour ``recursive=False`` (nothing past the next
  ``/``) and return nothing for ``max_results <= 0``. ``uploaded_at`` is the destination's mtime.
- Streams use ``shutil.copyfileobj``'s bounded buffer and are never closed here.
- Custom object metadata lives outside every container, at ``<root>/.bioaf-object-metadata/<container>/<key>``
  (directory 0700, records 0600, replaced atomically), so no listing sees it and no URI can address it. It
  follows its object: a write drops the old record before the bytes change and writes the new one after, so an
  interruption leaves an object with no metadata rather than with someone else's; copy carries it (or clears the
  destination's), and delete removes it.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from typing import BinaryIO

from app.adapters.models import StorageObjectNotFound, StoredObject, validate_object_metadata
from app.exceptions import ValidationError

METADATA_DIR = ".bioaf-object-metadata"


class LocalObjectFilesystem:
    def __init__(self, root: str, parse_uri: Callable[[str], tuple[str, str]], build_uri: Callable[[str, str], str]):
        self._root = root
        self._parse_uri = parse_uri
        self._build_uri = build_uri

    # -- resolution -------------------------------------------------------------------------

    def _base(self) -> str:
        return os.path.realpath(self._root)

    def _inside(self, real: str) -> bool:
        base = self._base()
        return real == base or real.startswith(base + os.sep)

    def _container_root(self, container: str, uri: str) -> str:
        if not container or container in (".", "..", METADATA_DIR) or any(c in container for c in ("/", "\\", "\0")):
            raise ValidationError(f"Storage URI names an unsafe container: {uri!r}")
        return os.path.join(self._base(), container)

    def _lexical(self, uri: str, *, directory: bool = False) -> str:
        """The unresolved path for ``uri`` after checking its container and key segments.

        ``directory`` accepts a trailing separator (a listing prefix or a directory operand).
        """
        container, key = self._parse_uri(uri)
        root = self._container_root(container, uri)
        segments = key.split("/")
        if directory and segments[-1] == "":
            segments = segments[:-1]
        if "\0" in key or key.startswith("/") or any(seg in (".", "..") for seg in segments):
            raise ValidationError(f"Storage URI escapes the object root: {uri!r}")
        if any(seg == "" for seg in segments) and segments != [""]:
            raise ValidationError(f"Storage URI has an empty key segment: {uri!r}")
        return os.path.join(root, *[seg for seg in segments if seg])

    def path(self, uri: str, *, directory: bool = False) -> str:
        """The real path for ``uri``, confined under the root (``ValidationError`` otherwise)."""
        real = os.path.realpath(self._lexical(uri, directory=directory))
        if not self._inside(real):
            raise ValidationError(f"Storage path escapes the object root: {uri!r}")
        return real

    # -- metadata records ---------------------------------------------------------------------

    def _metadata_path(self, uri: str) -> str:
        """Where ``uri``'s metadata record lives; the key was already confined by ``_lexical``."""
        container, key = self._parse_uri(uri)
        self._lexical(uri)
        return os.path.join(self._base(), METADATA_DIR, container, *[seg for seg in key.split("/") if seg])

    def _clear_metadata(self, uri: str) -> None:
        try:
            os.unlink(self._metadata_path(uri))
        except FileNotFoundError:
            pass

    def _write_metadata(self, uri: str, metadata: dict[str, str] | None) -> None:
        if not metadata:
            return
        path = self._metadata_path(uri)
        top = os.path.join(self._base(), METADATA_DIR)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        os.chmod(top, 0o700)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")  # 0600 whatever the umask
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(metadata, f)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def read_metadata(self, uri: str) -> dict[str, str]:
        self._file(uri)
        try:
            with open(self._metadata_path(uri)) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _file(self, uri: str) -> str:
        path = self.path(uri)
        if not os.path.isfile(path):
            raise StorageObjectNotFound(uri)
        return path

    # -- reads ------------------------------------------------------------------------------

    def _open(self, uri: str) -> BinaryIO:
        path = self._file(uri)
        try:
            return open(path, "rb")
        except (FileNotFoundError, IsADirectoryError) as e:  # gone between the check and the open
            raise StorageObjectNotFound(uri) from e

    def read_bytes(self, uri: str) -> bytes:
        with self._open(uri) as f:
            return f.read()

    def read_prefix(self, uri: str, length: int) -> bytes:
        if length < 0:
            raise ValueError("A prefix length cannot be negative")
        if length == 0:
            return b""
        with self._open(uri) as f:
            return f.read(length)

    def stream_into(self, uri: str, file_obj: BinaryIO) -> None:
        with self._open(uri) as f:
            shutil.copyfileobj(f, file_obj)

    def download_to_filename(self, uri: str, local_path: str) -> None:
        src = self._file(uri)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            shutil.copyfile(src, local_path)
        except FileNotFoundError as e:
            raise StorageObjectNotFound(uri) from e

    def exists(self, uri: str) -> bool:
        return os.path.isfile(self.path(uri))

    def size(self, uri: str) -> int:
        path = self._file(uri)
        try:
            return os.path.getsize(path)
        except FileNotFoundError as e:
            raise StorageObjectNotFound(uri) from e

    # -- writes -----------------------------------------------------------------------------

    def _writable(self, uri: str) -> str:
        path = self.path(uri)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def write_bytes(self, uri: str, data: bytes) -> None:
        path = self._writable(uri)
        self._clear_metadata(uri)
        with open(path, "wb") as f:
            f.write(data)

    def write_stream(self, uri: str, file_obj: BinaryIO, metadata: dict[str, str] | None = None) -> None:
        metadata = validate_object_metadata(metadata)
        # Rewind a seekable stream (the upload is the whole object); a pipe is read from where it is.
        try:
            file_obj.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        path = self._writable(uri)
        self._clear_metadata(uri)
        with open(path, "wb") as f:
            shutil.copyfileobj(file_obj, f)
        self._write_metadata(uri, metadata)

    def upload_filename(self, uri: str, local_path: str, metadata: dict[str, str] | None = None) -> None:
        metadata = validate_object_metadata(metadata)
        path = self._writable(uri)
        self._clear_metadata(uri)
        shutil.copyfile(local_path, path)
        self._write_metadata(uri, metadata)

    def delete(self, uri: str) -> None:
        """Remove the object; idempotent. A symlink is unlinked itself, never its target."""
        lexical = self._lexical(uri)
        self.path(uri)  # containment of what the link points at, too
        if os.path.islink(lexical) or os.path.isfile(lexical):
            try:
                os.unlink(lexical)
            except FileNotFoundError:
                pass
        self._clear_metadata(uri)

    def copy(self, source_uri: str, dest_uri: str) -> None:
        src = self._file(source_uri)
        dest = self._writable(dest_uri)
        if os.path.exists(dest) and os.path.samefile(src, dest):
            return
        metadata = self.read_metadata(source_uri)
        self._clear_metadata(dest_uri)
        try:
            shutil.copyfile(src, dest)
        except FileNotFoundError as e:
            raise StorageObjectNotFound(source_uri) from e
        self._write_metadata(dest_uri, metadata)

    def move(self, source_uri: str, dest_uri: str) -> None:
        """Copy, verify, then delete the source; any failure leaves the source in place."""
        src = self._file(source_uri)
        dest = self.path(dest_uri)
        if os.path.exists(dest) and os.path.samefile(src, dest):
            return
        self.copy(source_uri, dest_uri)
        if not os.path.isfile(dest):
            raise RuntimeError(f"Move verification failed: {dest_uri!r} not written")
        self.delete(source_uri)

    # -- listing ----------------------------------------------------------------------------

    def list(self, uri_prefix: str, *, recursive: bool = True, max_results: int | None = None) -> list[StoredObject]:
        container, key_prefix = self._parse_uri(uri_prefix)
        container_root = self._container_root(container, uri_prefix)
        self._lexical(uri_prefix, directory=True)  # the prefix itself may not escape
        if max_results is not None and max_results <= 0:
            return []
        real_root = os.path.realpath(container_root)
        if not os.path.isdir(real_root) or not self._inside(real_root):
            return []

        found: list[tuple[str, str]] = []
        for dirpath, dirnames, filenames in os.walk(container_root):
            dirnames.sort()
            for name in filenames:
                full = os.path.join(dirpath, name)
                key = os.path.relpath(full, container_root).replace(os.sep, "/")
                if not key.startswith(key_prefix):
                    continue
                if not recursive and "/" in key[len(key_prefix) :]:
                    continue
                real = os.path.realpath(full)
                if not self._inside(real) or not os.path.isfile(real):
                    continue  # a link out of the root, or to something that is not a file
                found.append((key, real))
        found.sort()
        if max_results is not None:
            found = found[:max_results]

        results: list[StoredObject] = []
        for key, real in found:
            try:
                stat = os.stat(real)
            except FileNotFoundError:
                continue  # removed while listing
            results.append(
                StoredObject(
                    filename=key.rsplit("/", 1)[-1],
                    storage_uri=self._build_uri(container, key),
                    size_bytes=stat.st_size,
                    # Writes create or truncate the destination (copies never carry the source's
                    # times), so the mtime is when the current object was written.
                    uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
        return results
