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
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import IO, Callable, Iterable, Literal, Protocol, cast
from urllib.parse import urlparse

logger = logging.getLogger("bioaf.reference_importer")

# Retry policy for transient mid-stream network failures (e.g., a public CDN
# closing the TCP connection during a multi-GB download). We re-issue the
# whole GET; range-resume is left for a future iteration if bandwidth waste
# becomes a concern.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 5.0


def _build_transient_network_exceptions() -> tuple[type[BaseException], ...]:
    """The exception types we treat as 'connection blip, retry the whole GET'.

    httpx wraps httpcore exceptions most of the time but the wrapping is not
    consistent across all stream-iteration paths, so we catch both
    libraries' versions plus the OS-level ConnectionError that bubbles up
    when a peer hangs up before httpx has translated the failure.
    """
    excs: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        import httpx

        excs.extend(
            [
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.TimeoutException,
            ]
        )
    except ImportError:
        # `httpx` is optional in some environments; fall back to stdlib/network
        # exception classes only.
        pass
    try:
        import httpcore

        excs.extend(
            [
                httpcore.RemoteProtocolError,
                httpcore.ConnectError,
                httpcore.ReadError,
                httpcore.WriteError,
                httpcore.ConnectTimeout,
                httpcore.ReadTimeout,
            ]
        )
    except ImportError:
        # `httpcore` may not be installed directly; continue with available
        # exception classes.
        pass
    return tuple(excs)


_TRANSIENT_NETWORK_EXCEPTIONS = _build_transient_network_exceptions()

# GCS resumable-upload chunk size. Must be a multiple of 256 KiB. Picked at
# 8 MiB so a multi-gig download streams through the backend's 512 MB
# container without buffering the whole payload in memory: the SDK PUTs
# each 8 MiB chunk to GCS as soon as it has been read from the source URL.
_GCS_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


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


def _md5_of_file(path: str) -> str:
    """Return the hex md5 of a file by streaming it from disk."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


_CONTENT_RANGE_RE = re.compile(r"bytes\s+\d+\s*-\s*\d+\s*/\s*(\d+)", re.IGNORECASE)


def _parse_content_range_total(header_value: str) -> int | None:
    """Pull the total size out of a `Content-Range: bytes A-B/TOTAL` header.

    Returns None for the rare `bytes A-B/*` form where the server doesn't
    know the total; the caller falls back to content-length + bytes_have.
    """
    if not header_value:
        return None
    m = _CONTENT_RANGE_RE.search(header_value)
    if not m:
        return None
    return int(m.group(1))


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
        # Stage the source on local disk first so a mid-stream connection
        # drop can be recovered with an HTTP `Range: bytes=N-` request
        # (the importer's previous design streamed straight into GCS,
        # which made true resume impossible: a GCS resumable upload
        # session can't be continued from an arbitrary new GET).
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="bioaf-refimport-")
        os.close(tmp_fd)
        try:
            total_bytes, computed_md5 = self._download_with_resume(tmp_path)

            if cfg.source_md5_url:
                self._callback(
                    status="verifying",
                    progress_pct=100,
                    bytes_downloaded=total_bytes,
                    total_bytes=total_bytes,
                )
                expected = self._fetch_expected_md5(cfg.source_md5_url)
                if expected != computed_md5:
                    msg = f"md5 mismatch: expected {expected} got {computed_md5}"
                    self._callback(status="failed", error_message=msg)
                    raise Md5MismatchError(msg)

            files_written = self._upload_from_local(tmp_path, total_bytes)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        self._callback(
            status="active",
            progress_pct=100,
            bytes_downloaded=total_bytes,
            total_bytes=total_bytes,
        )

        return ImportResult(
            files=[
                ImportedFile(
                    filename=name,
                    gcs_uri=f"gs://{cfg.gcs_bucket}/{cfg.gcs_prefix.rstrip('/') + '/' + name}",
                    size_bytes=size_hint if size_hint is not None else total_bytes,
                    md5=computed_md5 if cfg.extract_mode == "none" else None,
                )
                for name, _blob, size_hint in files_written
            ]
        )

    def _download_with_resume(self, tmp_path: str) -> tuple[int, str]:
        """Stream the source URL to a local file, resuming with a
        `Range: bytes=<bytes_have>-` header after a transient connection
        drop. Returns (total_bytes_downloaded, md5_hex) once the file is
        fully written. Raises the last transient exception (or any
        non-transient one) after exhausting _MAX_ATTEMPTS.
        """
        cfg = self._cfg
        base_headers: dict[str, str] = {}
        if cfg.auth_header:
            base_headers["Authorization"] = cfg.auth_header

        bytes_have = 0
        total_bytes: int | None = None
        last_exc: Exception | None = None

        # Truncate any stale tempfile from before.
        with open(tmp_path, "wb"):
            pass

        self._callback(status="downloading", progress_pct=0, bytes_downloaded=0, total_bytes=None)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            headers = dict(base_headers)
            if bytes_have > 0:
                headers["Range"] = f"bytes={bytes_have}-"

            try:
                with self._http.stream("GET", cfg.source_url, headers=headers) as response:
                    response.raise_for_status()

                    appending = False
                    if bytes_have > 0:
                        if response.status_code == 206:
                            appending = True
                        else:
                            # Server ignored the Range header and is
                            # re-sending the full body. Throw away what
                            # we have on disk and accept the fresh stream.
                            logger.info(
                                "Reference import %d: server returned %d to Range request; restarting from byte 0",
                                cfg.reference_id,
                                response.status_code,
                            )
                            bytes_have = 0
                            with open(tmp_path, "wb"):
                                pass

                    if total_bytes is None:
                        if response.status_code == 206:
                            total_bytes = _parse_content_range_total(response.headers.get("content-range", ""))
                        if total_bytes is None:
                            cl = response.headers.get("content-length")
                            if cl:
                                total_bytes = int(cl) + (bytes_have if appending else 0)

                    mode = "ab" if appending else "wb"
                    with open(tmp_path, mode) as f:
                        for chunk in response.iter_bytes(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            f.write(chunk)
                            bytes_have += len(chunk)
                            pct = int(bytes_have * 100 / total_bytes) if total_bytes else None
                            self._callback(
                                status="downloading",
                                progress_pct=pct,
                                bytes_downloaded=bytes_have,
                                total_bytes=total_bytes,
                            )

                # Download complete.
                return bytes_have, _md5_of_file(tmp_path)
            except _TRANSIENT_NETWORK_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    backoff = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    bytes_have = os.path.getsize(tmp_path)
                    logger.warning(
                        "Reference import %d attempt %d/%d dropped at %d bytes (%s); resuming in %.0fs",
                        cfg.reference_id,
                        attempt,
                        _MAX_ATTEMPTS,
                        bytes_have,
                        exc,
                        backoff,
                    )
                    self._callback(
                        status="downloading",
                        progress_pct=int(bytes_have * 100 / total_bytes) if total_bytes else None,
                        bytes_downloaded=bytes_have,
                        total_bytes=total_bytes,
                        error_message=f"Connection dropped at {bytes_have} bytes (attempt {attempt}/{_MAX_ATTEMPTS}); resuming",
                    )
                    time.sleep(backoff)
                    continue
                break

        assert last_exc is not None
        msg = f"Source connection dropped after {_MAX_ATTEMPTS} attempts: {last_exc}"
        self._callback(status="failed", error_message=msg)
        raise last_exc

    def _upload_from_local(self, tmp_path: str, total_bytes: int) -> list[tuple[str, object, int | None]]:
        """Upload the staged file to GCS based on extract_mode. Each blob
        gets a fixed chunk_size so the SDK uses a resumable upload and
        never tries to buffer the whole body in memory."""
        cfg = self._cfg
        filename = _filename_from_url(cfg.source_url)
        bucket = self._storage.bucket(cfg.gcs_bucket)
        prefix = cfg.gcs_prefix.rstrip("/") + "/"

        files_written: list[tuple[str, object, int | None]] = []

        def _new_blob(name: str):
            b = bucket.blob(name)
            b.chunk_size = _GCS_UPLOAD_CHUNK_SIZE
            return b

        if cfg.extract_mode == "none":
            self._callback(status="finalizing", bytes_downloaded=total_bytes, total_bytes=total_bytes)
            blob = _new_blob(prefix + filename)
            with open(tmp_path, "rb") as f:
                blob.upload_from_file(f, content_type="application/octet-stream", size=total_bytes)
            files_written.append((filename, blob, total_bytes))
        elif cfg.extract_mode == "gzip":
            self._callback(status="extracting", bytes_downloaded=total_bytes, total_bytes=total_bytes)
            import gzip

            inner = filename[:-3] if filename.endswith(".gz") else filename
            blob = _new_blob(prefix + inner)
            with open(tmp_path, "rb") as f:
                gz = gzip.GzipFile(fileobj=cast(IO[bytes], f), mode="rb")
                counter = _CountingReader(gz)
                blob.upload_from_file(counter, content_type="application/octet-stream")
            files_written.append((inner, blob, counter.bytes_read))
        elif cfg.extract_mode in ("tar", "tar.gz"):
            self._callback(status="extracting", bytes_downloaded=total_bytes, total_bytes=total_bytes)
            import tarfile

            tar_mode: Literal["r|", "r|gz"] = "r|" if cfg.extract_mode == "tar" else "r|gz"
            with open(tmp_path, "rb") as f:
                with tarfile.open(fileobj=cast(IO[bytes], f), mode=tar_mode) as tf:
                    for member in tf:
                        if not member.isfile():
                            continue
                        blob = _new_blob(prefix + member.name)
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        blob.upload_from_file(src, content_type="application/octet-stream", size=member.size)
                        files_written.append((member.name, blob, member.size))
        else:
            raise ValueError(f"Unsupported extract_mode: {cfg.extract_mode!r}")

        return files_written

    def _fetch_expected_md5(self, url: str) -> str:
        with self._http.stream("GET", url, headers=None) as response:
            response.raise_for_status()
            body = b"".join(response.iter_bytes(chunk_size=4096))
        return _parse_md5_file(body)
