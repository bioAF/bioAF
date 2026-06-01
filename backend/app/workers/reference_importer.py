"""Reference-data URL import worker.

Runs as an in-process asyncio background task scheduled by
`ReferenceDataService._schedule_import`. Streams the source URL straight
into the org's reference GCS bucket, optionally verifies an upstream MD5
file, optionally extracts gzip / tar / tar.gz archives, and reports
progress via a caller-supplied callback so the service layer can write
updates to the `ReferenceImportProgress` row.

HTTP, GCS, and the callback are injectable for tests. See
backend/tests/test_reference_importer_worker.py.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol
from urllib.parse import urlparse


@dataclass
class ImporterConfig:
    reference_id: int
    source_url: str
    gcs_bucket: str
    gcs_prefix: str
    # Unused in-process; retained so the dataclass shape stays stable for
    # any future out-of-process deployment that needs a callback URL.
    callback_url: str = ""
    internal_token: str = ""
    source_md5_url: str | None = None
    auth_header: str | None = None
    extract_mode: str = "none"


@dataclass
class ImportedFile:
    filename: str
    gcs_uri: str
    size_bytes: int
    md5: str | None = None


@dataclass
class ImportResult:
    files: list[ImportedFile] = field(default_factory=list)


# A small Protocol surface so tests can pass fakes.


class _StreamingResponse(Protocol):
    status_code: int
    headers: dict

    def __enter__(self): ...
    def __exit__(self, *exc): ...
    def iter_bytes(self, chunk_size: int | None = None) -> Iterable[bytes]: ...
    def raise_for_status(self) -> None: ...


class _HttpClient(Protocol):
    def stream(self, method: str, url: str, *, headers=None, follow_redirects: bool = True) -> _StreamingResponse: ...


# Progress callback: same shape as ReferenceImportProgressUpdate.
ProgressCallback = Callable[..., None]


class _CountingReader:
    """File-like wrapper that delegates read() to another file-like and
    counts the bytes that flow through. Used to size GCS uploads whose total
    size we don't know up-front (e.g., the output of a gzip decompressor)."""

    def __init__(self, inner):
        self._inner = inner
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size) if size >= 0 else self._inner.read()
        if chunk:
            self.bytes_read += len(chunk)
        return chunk


class _ProgressFileReader:
    """Wraps an HTTP byte iterator as a file-like with a `read(n)` interface,
    reporting bytes read via a callback and computing an MD5 hash of the
    stream inline. Used to feed the GCS uploader so we can stream the source
    URL straight to GCS without buffering the full payload in memory or on
    disk."""

    def __init__(self, chunks: Iterable[bytes], on_progress: Callable[[int], None]):
        self._iter = iter(chunks)
        self._buf = bytearray()
        self._on_progress = on_progress
        self._hash = hashlib.md5()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            for chunk in self._iter:
                self._buf.extend(chunk)
            out = bytes(self._buf)
            self._buf = bytearray()
        else:
            while len(self._buf) < size:
                try:
                    self._buf.extend(next(self._iter))
                except StopIteration:
                    break
            out = bytes(self._buf[:size])
            del self._buf[:size]
        if out:
            self.bytes_read += len(out)
            self._hash.update(out)
            self._on_progress(self.bytes_read)
        return out

    @property
    def md5_hex(self) -> str:
        return self._hash.hexdigest()


def _parse_md5_file(body: bytes) -> str:
    """Parse a coreutils-style md5 file: '<hex>  <filename>' on the first
    non-blank line. Falls back to a bare 32-hex-character line."""

    for raw in body.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^([a-fA-F0-9]{32})(?:\s+\*?(.+))?$", line)
        if m:
            return m.group(1).lower()
        break
    raise ValueError("Could not parse md5 file")


class Md5MismatchError(Exception):
    """Raised when the streamed payload's md5 disagrees with the upstream md5 file."""


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] or "download"


class ReferenceImporter:
    """Streams a source URL into GCS under config.gcs_prefix, with progress."""

    def __init__(
        self,
        config: ImporterConfig,
        *,
        http_client: _HttpClient,
        storage_client,
        callback: ProgressCallback,
    ):
        self._cfg = config
        self._http = http_client
        self._storage = storage_client
        self._callback = callback

    def run(self) -> ImportResult:
        try:
            return self._run_inner()
        except Md5MismatchError:
            # Md5MismatchError already emitted a 'failed' callback.
            raise
        except Exception as exc:
            self._callback(status="failed", error_message=str(exc) or exc.__class__.__name__)
            raise

    def _run_inner(self) -> ImportResult:
        cfg = self._cfg
        filename = _filename_from_url(cfg.source_url)
        headers = {"Authorization": cfg.auth_header} if cfg.auth_header else None
        bucket = self._storage.bucket(cfg.gcs_bucket)
        prefix = cfg.gcs_prefix.rstrip("/") + "/"

        files_written: list[tuple[str, object, int]] = []

        with self._http.stream("GET", cfg.source_url, headers=headers) as response:
            response.raise_for_status()
            total_bytes = int(response.headers.get("content-length") or 0) or None

            self._callback(
                status="downloading",
                progress_pct=0,
                bytes_downloaded=0,
                total_bytes=total_bytes,
            )

            def _on_bytes(bytes_so_far: int) -> None:
                pct = int(bytes_so_far * 100 / total_bytes) if total_bytes else None
                self._callback(
                    status="downloading",
                    progress_pct=pct,
                    bytes_downloaded=bytes_so_far,
                    total_bytes=total_bytes,
                )

            reader = _ProgressFileReader(response.iter_bytes(chunk_size=64 * 1024), _on_bytes)

            if cfg.extract_mode == "none":
                blob_name = prefix + filename
                blob = bucket.blob(blob_name)
                blob.upload_from_file(reader, content_type="application/octet-stream")
                # size is settled after the stream completes (== bytes downloaded)
                files_written.append((filename, blob, None))
            elif cfg.extract_mode == "gzip":
                self._callback(status="extracting", bytes_downloaded=reader.bytes_read, total_bytes=total_bytes)
                import gzip

                inner = filename[:-3] if filename.endswith(".gz") else filename
                blob_name = prefix + inner
                blob = bucket.blob(blob_name)
                gz = gzip.GzipFile(fileobj=reader, mode="rb")
                counter = _CountingReader(gz)
                blob.upload_from_file(counter, content_type="application/octet-stream")
                files_written.append((inner, blob, counter.bytes_read))
            elif cfg.extract_mode in ("tar", "tar.gz"):
                self._callback(status="extracting", bytes_downloaded=reader.bytes_read, total_bytes=total_bytes)
                import tarfile

                mode = "r|" if cfg.extract_mode == "tar" else "r|gz"
                with tarfile.open(fileobj=reader, mode=mode) as tf:
                    for member in tf:
                        if not member.isfile():
                            continue
                        blob_name = prefix + member.name
                        blob = bucket.blob(blob_name)
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        blob.upload_from_file(src, content_type="application/octet-stream", size=member.size)
                        files_written.append((member.name, blob, member.size))
            else:
                raise ValueError(f"Unsupported extract_mode: {cfg.extract_mode!r}")

        downloaded = reader.bytes_read
        computed_md5 = reader.md5_hex

        if cfg.source_md5_url:
            self._callback(
                status="verifying",
                progress_pct=100,
                bytes_downloaded=downloaded,
                total_bytes=downloaded,
            )
            expected = self._fetch_expected_md5(cfg.source_md5_url)
            if expected != computed_md5:
                for _, blob, _size in files_written:
                    blob.delete()
                msg = f"md5 mismatch: expected {expected} got {computed_md5}"
                self._callback(status="failed", error_message=msg)
                raise Md5MismatchError(msg)

        self._callback(
            status="active",
            progress_pct=100,
            bytes_downloaded=downloaded,
            total_bytes=downloaded,
        )

        return ImportResult(
            files=[
                ImportedFile(
                    filename=name,
                    gcs_uri=f"gs://{cfg.gcs_bucket}/{prefix + name}",
                    size_bytes=size_hint if size_hint is not None else downloaded,
                    md5=computed_md5 if cfg.extract_mode == "none" else None,
                )
                for name, blob, size_hint in files_written
            ]
        )

    def _fetch_expected_md5(self, url: str) -> str:
        with self._http.stream("GET", url, headers=None) as response:
            response.raise_for_status()
            body = b"".join(response.iter_bytes(chunk_size=4096))
        return _parse_md5_file(body)
