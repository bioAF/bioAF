"""TDD: backend/app/workers/reference_importer.py — the import worker.

ReferenceImporter is run from an in-process asyncio background task in
the backend (scheduled by ReferenceDataService._schedule_import). It
streams the source URL straight into GCS, optionally verifies an upstream
MD5 file, optionally extracts gzip / tar / tar.gz archives, and reports
progress via a caller-supplied callback so the service layer can write
updates to the ReferenceImportProgress row.

These tests inject fake HTTP + GCS + callback dependencies so they assert
behavior (what the importer does), not implementation (which library it
uses).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


# --- Fakes -----------------------------------------------------------------


@dataclass
class _FakeResponse:
    """Stand-in for an httpx streaming response."""

    status_code: int = 200
    chunks: list[bytes] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    raise_on_enter: Exception | None = None

    def __enter__(self):
        if self.raise_on_enter is not None:
            raise self.raise_on_enter
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size: int | None = None):
        for chunk in self.chunks:
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttpClient:
    """Stand-in for httpx.Client. Records GET calls; returns the queued response."""

    def __init__(self, responses: dict[str, _FakeResponse]):
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def stream(self, method: str, url: str, *, headers=None, follow_redirects=True):
        self.calls.append((url, dict(headers) if headers else None))
        if url not in self._responses:
            raise AssertionError(f"unexpected GET {url}")
        return self._responses[url]


class _FakeBlob:
    def __init__(self, bucket_name: str, name: str):
        self.bucket_name = bucket_name
        self.name = name
        self.data = bytearray()
        self.md5_hash: str | None = None
        self.content_type: str | None = None
        self.deleted = False
        self.chunk_size: int | None = None

    @property
    def gcs_uri(self) -> str:
        return f"gs://{self.bucket_name}/{self.name}"

    def upload_from_file(self, fileobj, *, content_type=None, rewind=False, size=None):
        if rewind:
            fileobj.seek(0)
        self.content_type = content_type
        # Real GCS overwrites the object on every upload_from_file (a fresh
        # resumable session). Mirror that so retry tests see the final
        # body, not the concatenation of all attempts.
        self.data = bytearray()
        while True:
            chunk = fileobj.read(64 * 1024)
            if not chunk:
                break
            self.data.extend(chunk)

    def delete(self):
        self.deleted = True

    def reload(self):
        return None


class _FakeBucket:
    def __init__(self, name: str, store: "_FakeStorageClient"):
        self.name = name
        self._store = store

    def blob(self, name: str) -> _FakeBlob:
        if name not in self._store.blobs.setdefault(self.name, {}):
            self._store.blobs[self.name][name] = _FakeBlob(self.name, name)
        return self._store.blobs[self.name][name]

    def list_blobs(self, prefix: str = ""):
        return [b for n, b in self._store.blobs.get(self.name, {}).items() if n.startswith(prefix)]


class _FakeStorageClient:
    def __init__(self):
        self.blobs: dict[str, dict[str, _FakeBlob]] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(name, self)


class _RecordingCallback:
    """Captures progress callbacks the importer makes."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, **payload):
        # Caller passes a normalized dict matching ReferenceImportProgressUpdate.
        self.events.append(dict(payload))


# --- Helpers ---------------------------------------------------------------


def _make_config(**overrides):
    from app.workers.reference_importer import ImporterConfig

    base = dict(
        reference_id=42,
        source_url="https://ftp.example.org/data/refs/gencode.v45.gtf.gz",
        source_md5_url=None,
        auth_header=None,
        gcs_bucket="bioaf-references-test",
        gcs_prefix="annotation/gencode/v45/",
        extract_mode="none",
    )
    base.update(overrides)
    return ImporterConfig(**base)


# --- Tests -----------------------------------------------------------------


def test_streams_source_url_into_single_gcs_blob_and_reports_progress():
    """With extract=none and no MD5, the importer:

    - opens the source URL,
    - streams the body chunk-by-chunk into a single GCS blob whose name is
      the URL's basename under gcs_prefix,
    - reports at least three progress callbacks: downloading start,
      downloading mid, and active (success) at the end,
    - returns an ImportResult naming exactly one file.
    """
    from app.workers.reference_importer import ReferenceImporter

    payload = b"a" * (100 * 1024) + b"b" * (100 * 1024) + b"c" * 50_000  # ~250 KB
    chunks = [payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)]
    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=chunks,
                headers={"content-length": str(len(payload))},
            )
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()

    config = _make_config()
    importer = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback)

    result = importer.run()

    # Exactly one blob, named after the URL basename under the prefix.
    bucket_blobs = storage.blobs["bioaf-references-test"]
    assert list(bucket_blobs.keys()) == ["annotation/gencode/v45/gencode.v45.gtf.gz"]
    blob = bucket_blobs["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert bytes(blob.data) == payload

    # Progress: at least one 'downloading' and a terminal 'active'.
    statuses = [e["status"] for e in callback.events]
    assert "downloading" in statuses, statuses
    assert statuses[-1] == "active", statuses

    # Bytes downloaded monotonically non-decreasing, terminal matches total.
    bytes_seen: list[int] = [e["bytes_downloaded"] for e in callback.events if e.get("bytes_downloaded") is not None]
    assert bytes_seen == sorted(bytes_seen)
    assert bytes_seen[-1] == len(payload)

    # Result describes the single file.
    assert len(result.files) == 1
    f = result.files[0]
    assert f.filename == "gencode.v45.gtf.gz"
    assert f.gcs_uri == "gs://bioaf-references-test/annotation/gencode/v45/gencode.v45.gtf.gz"
    assert f.size_bytes == len(payload)


def test_verifies_md5_when_source_md5_url_provided():
    """When source_md5_url is set, the importer:

    - fetches the MD5 file separately,
    - computes the streamed payload's MD5 inline,
    - emits a `verifying` callback,
    - reports `active` on match and records the md5 on the ImportedFile.
    """
    import hashlib

    from app.workers.reference_importer import ReferenceImporter

    payload = b"GENCODE GTF body, totally legit\n" * 1024
    expected_md5 = hashlib.md5(payload).hexdigest()
    md5_file_body = f"{expected_md5}  gencode.v45.gtf.gz\n".encode()

    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=[payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)],
                headers={"content-length": str(len(payload))},
            ),
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz.md5": _FakeResponse(
                status_code=200,
                chunks=[md5_file_body],
                headers={"content-length": str(len(md5_file_body))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(
        source_md5_url="https://ftp.example.org/data/refs/gencode.v45.gtf.gz.md5",
    )
    importer = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback)

    result = importer.run()

    statuses = [e["status"] for e in callback.events]
    assert "verifying" in statuses, statuses
    assert statuses[-1] == "active"

    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert blob.deleted is False
    assert bytes(blob.data) == payload
    assert result.files[0].md5 == expected_md5


def test_reports_failed_when_md5_mismatches_and_no_blob_is_uploaded():
    """If the upstream md5 file disagrees with the downloaded payload, the
    importer reports failure with a descriptive error_message and never
    uploads to GCS in the first place. The download stages to a local
    file, md5 is verified against that file, and only on a match does
    the upload phase begin."""
    from app.workers.reference_importer import ReferenceImporter

    payload = b"this body does not match the md5 file" * 1000
    wrong_md5 = "0" * 32
    md5_file_body = f"{wrong_md5}  gencode.v45.gtf.gz\n".encode()

    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=[payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)],
                headers={"content-length": str(len(payload))},
            ),
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz.md5": _FakeResponse(
                status_code=200,
                chunks=[md5_file_body],
                headers={"content-length": str(len(md5_file_body))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(
        source_md5_url="https://ftp.example.org/data/refs/gencode.v45.gtf.gz.md5",
    )
    importer = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback)

    with pytest.raises(Exception):
        importer.run()

    failure = callback.events[-1]
    assert failure["status"] == "failed"
    assert "md5" in (failure.get("error_message") or "").lower()

    # No blob was uploaded -- verify happens before the upload phase.
    assert storage.blobs == {}


def test_extract_gzip_writes_decompressed_object_with_stripped_extension():
    """extract_mode='gzip': source URL ends in .gz; importer writes the
    decompressed body as a single blob with the '.gz' suffix stripped from
    the filename."""
    import gzip
    import io

    from app.workers.reference_importer import ReferenceImporter

    decompressed = b"chr1\t1\t1000\tregion-A\n" * 5000
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(decompressed)
    gz_bytes = buf.getvalue()

    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=[gz_bytes[i : i + 64 * 1024] for i in range(0, len(gz_bytes), 64 * 1024)],
                headers={"content-length": str(len(gz_bytes))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(extract_mode="gzip")
    result = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback).run()

    blobs = storage.blobs["bioaf-references-test"]
    assert list(blobs.keys()) == ["annotation/gencode/v45/gencode.v45.gtf"]
    assert bytes(blobs["annotation/gencode/v45/gencode.v45.gtf"].data) == decompressed

    statuses = [e["status"] for e in callback.events]
    assert "extracting" in statuses, statuses
    assert statuses[-1] == "active"

    assert [f.filename for f in result.files] == ["gencode.v45.gtf"]
    assert result.files[0].size_bytes == len(decompressed)


def test_extract_tar_writes_each_member_as_its_own_blob():
    """extract_mode='tar': importer streams the archive, writes each tar
    member to a separate blob under gcs_prefix, and returns one ImportedFile
    per member."""
    import io
    import tarfile

    from app.workers.reference_importer import ReferenceImporter

    members = {
        "ref_a.txt": b"alpha contents " * 1000,
        "subdir/ref_b.tsv": b"col1\tcol2\n" * 2000,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, body in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    tar_bytes = buf.getvalue()

    url = "https://ftp.example.org/data/refs/pack.tar"
    http = _FakeHttpClient(
        {
            url: _FakeResponse(
                status_code=200,
                chunks=[tar_bytes[i : i + 64 * 1024] for i in range(0, len(tar_bytes), 64 * 1024)],
                headers={"content-length": str(len(tar_bytes))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(source_url=url, extract_mode="tar")
    result = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback).run()

    bucket_blobs = storage.blobs["bioaf-references-test"]
    assert sorted(bucket_blobs.keys()) == [
        "annotation/gencode/v45/ref_a.txt",
        "annotation/gencode/v45/subdir/ref_b.tsv",
    ]
    assert bytes(bucket_blobs["annotation/gencode/v45/ref_a.txt"].data) == members["ref_a.txt"]
    assert bytes(bucket_blobs["annotation/gencode/v45/subdir/ref_b.tsv"].data) == members["subdir/ref_b.tsv"]

    statuses = [e["status"] for e in callback.events]
    assert "extracting" in statuses
    assert statuses[-1] == "active"

    assert sorted(f.filename for f in result.files) == ["ref_a.txt", "subdir/ref_b.tsv"]


def test_source_url_non_200_reports_failed_and_records_status_code():
    """If the source URL returns a non-2xx, the importer emits a `failed`
    callback with an error_message that includes the upstream status and
    raises so the Job exits non-zero. No GCS objects are written."""
    from app.workers.reference_importer import ReferenceImporter

    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=404,
                chunks=[b""],
                headers={},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    importer = ReferenceImporter(_make_config(), http_client=http, storage_client=storage, callback=callback)

    with pytest.raises(Exception):
        importer.run()

    failure = callback.events[-1]
    assert failure["status"] == "failed"
    assert "404" in (failure.get("error_message") or "")
    assert storage.blobs == {}


def test_passes_auth_header_to_source_request_when_provided():
    """When config.auth_header is set, the importer sends it as the
    Authorization header on the source URL request. The md5 URL fetch
    does NOT receive it (the md5 file may live on a different host or
    not need auth)."""
    from app.workers.reference_importer import ReferenceImporter

    payload = b"x" * 4096
    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=[payload],
                headers={"content-length": str(len(payload))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(auth_header="Bearer secret-token")

    ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback).run()

    # Find the source URL call
    src_calls = [c for c in http.calls if c[0] == config.source_url]
    assert len(src_calls) == 1
    _, headers = src_calls[0]
    assert headers is not None
    assert headers.get("Authorization") == "Bearer secret-token"


def test_resumes_with_range_header_after_connection_drop(monkeypatch):
    """When the source CDN drops the connection partway through, the
    importer's second GET sets a `Range: bytes=N-` header so the server
    only re-sends the remaining bytes, not the whole file from byte 0.
    Bandwidth on a 10+ GB reference matters."""
    import httpcore

    from app.workers.reference_importer import ReferenceImporter

    monkeypatch.setattr("time.sleep", lambda *_: None)

    payload = b"abcdefgh" * 50_000  # 400 KB, deterministic content
    drop_at = 128 * 1024  # break after 128 KB
    url = "https://ftp.example.org/data/refs/gencode.v45.gtf.gz"

    requests: list[tuple[str, dict[str, str] | None]] = []

    class _Http:
        def stream(self, method, u, *, headers=None, follow_redirects=True):
            captured = dict(headers) if headers else None
            requests.append((u, captured))
            if len(requests) == 1:

                class _Drop(_FakeResponse):
                    def iter_bytes(self_inner, chunk_size=None):
                        size = chunk_size or 64 * 1024
                        for i in range(0, drop_at, size):
                            yield payload[i : i + size]
                        raise httpcore.RemoteProtocolError("peer closed connection")

                return _Drop(
                    status_code=200,
                    chunks=[],
                    headers={"content-length": str(len(payload)), "accept-ranges": "bytes"},
                )

            # Second request: must be a Range request
            assert captured is not None
            assert captured.get("Range") == f"bytes={drop_at}-", captured
            return _FakeResponse(
                status_code=206,
                chunks=[payload[drop_at:]],
                headers={
                    "content-length": str(len(payload) - drop_at),
                    "content-range": f"bytes {drop_at}-{len(payload) - 1}/{len(payload)}",
                },
            )

    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    ReferenceImporter(
        _make_config(source_url=url),
        http_client=_Http(),
        storage_client=storage,
        callback=callback,
    ).run()

    # Final blob has the FULL payload, no duplicates, no missing bytes.
    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert bytes(blob.data) == payload
    # Two GETs total: the initial and the Range continuation.
    assert len(requests) == 2


def test_falls_back_to_full_restart_when_server_ignores_range_header(monkeypatch):
    """Some servers (and CDN edges) ignore the Range header and return
    200 OK with the full body instead of 206 Partial Content. The
    importer must detect this, throw away the partial bytes already on
    disk, and accept the full re-sent body so the final object is
    still correct."""
    import httpcore

    from app.workers.reference_importer import ReferenceImporter

    monkeypatch.setattr("time.sleep", lambda *_: None)

    payload = b"y" * 200_000
    drop_at = 100_000
    url = "https://ftp.example.org/data/refs/gencode.v45.gtf.gz"

    class _Http:
        attempts = 0

        def stream(self_outer, method, u, *, headers=None, follow_redirects=True):
            type(self_outer).attempts += 1
            if type(self_outer).attempts == 1:

                class _Drop(_FakeResponse):
                    def iter_bytes(self_inner, chunk_size=None):
                        size = chunk_size or 64 * 1024
                        for i in range(0, drop_at, size):
                            yield payload[i : i + size]
                        raise httpcore.RemoteProtocolError("peer closed connection")

                return _Drop(
                    status_code=200,
                    chunks=[],
                    headers={"content-length": str(len(payload))},
                )
            # Server ignored the Range header and returned 200 with the
            # full body. The importer should accept this gracefully.
            return _FakeResponse(
                status_code=200,
                chunks=[payload],
                headers={"content-length": str(len(payload))},
            )

    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    ReferenceImporter(
        _make_config(source_url=url),
        http_client=_Http(),
        storage_client=storage,
        callback=callback,
    ).run()

    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert bytes(blob.data) == payload


def test_retries_when_source_connection_drops_mid_stream_then_succeeds(monkeypatch):
    """Public CDNs (10x Genomics, Ensembl FTP, ...) routinely drop the TCP
    connection mid-stream during multi-GB transfers. The importer must
    re-issue the GET on a transient network error and run the import to
    completion instead of failing the whole reference dataset. We keep
    this naive (restart from byte 0) for now; a Range-resume can come
    later if bandwidth becomes an issue."""
    import httpcore

    from app.workers.reference_importer import ReferenceImporter

    # No real backoff in tests.
    monkeypatch.setattr("time.sleep", lambda *_: None)

    payload = b"x" * 200_000
    chunks = [payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)]
    url = "https://ftp.example.org/data/refs/gencode.v45.gtf.gz"

    class _FlakyResponse(_FakeResponse):
        def iter_bytes(self, chunk_size=None):
            # Emit a couple of chunks, then simulate the CDN hanging up.
            for c in self.chunks[:2]:
                yield c
            raise httpcore.RemoteProtocolError("peer closed connection")

    success_response = _FakeResponse(
        status_code=200,
        chunks=chunks,
        headers={"content-length": str(len(payload))},
    )
    attempts: list[int] = []

    class _RetryingHttp:
        def stream(self, method, url_arg, *, headers=None, follow_redirects=True):
            attempts.append(1)
            if len(attempts) == 1:
                return _FlakyResponse(
                    status_code=200,
                    chunks=chunks,
                    headers={"content-length": str(len(payload))},
                )
            return success_response

    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    importer = ReferenceImporter(
        _make_config(source_url=url),
        http_client=_RetryingHttp(),
        storage_client=storage,
        callback=callback,
    )
    importer.run()

    # Two attempts: the first dropped, the second completed.
    assert len(attempts) == 2
    # And the dataset ended in 'active' with the full payload uploaded.
    statuses = [e["status"] for e in callback.events]
    assert statuses[-1] == "active", statuses
    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert bytes(blob.data) == payload


def test_gives_up_after_max_retries_on_persistent_connection_drops(monkeypatch):
    """If every attempt fails with a transient network error, the importer
    reports 'failed' and re-raises so the background task in the service
    layer can clean up. The final error_message should include the last
    upstream error so the user can see WHY it failed."""
    import httpcore

    from app.workers.reference_importer import ReferenceImporter

    monkeypatch.setattr("time.sleep", lambda *_: None)

    url = "https://ftp.example.org/data/refs/gencode.v45.gtf.gz"

    class _AlwaysFlaky:
        attempts = 0

        def stream(self, method, url_arg, *, headers=None, follow_redirects=True):
            type(self).attempts += 1

            class _R(_FakeResponse):
                def iter_bytes(self, chunk_size=None):
                    yield b"x" * 64
                    raise httpcore.RemoteProtocolError("peer closed connection")

            return _R(status_code=200, chunks=[b""], headers={"content-length": "100"})

    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    importer = ReferenceImporter(
        _make_config(source_url=url),
        http_client=_AlwaysFlaky(),
        storage_client=storage,
        callback=callback,
    )

    with pytest.raises(Exception):
        importer.run()

    # Multiple attempts before giving up.
    assert _AlwaysFlaky.attempts >= 3
    last = callback.events[-1]
    assert last["status"] == "failed"
    assert "peer closed connection" in (last.get("error_message") or "")


def test_blob_chunk_size_is_set_so_uploads_stream_in_chunks_not_buffered_in_memory():
    """For multi-gig downloads on the 512MB backend container, the GCS
    upload must be chunked / resumable. Setting blob.chunk_size before
    upload_from_file tells google-cloud-storage to PUT each chunk to GCS
    as soon as it has chunk_size bytes, rather than buffering the entire
    body in memory. Without this, large imports stall: the SDK
    back-pressures the reader and progress callbacks stop firing well
    before the body finishes."""
    from app.workers.reference_importer import ReferenceImporter

    payload = b"x" * (2 * 1024 * 1024)  # 2 MB, plenty to verify the wiring
    http = _FakeHttpClient(
        {
            "https://ftp.example.org/data/refs/gencode.v45.gtf.gz": _FakeResponse(
                status_code=200,
                chunks=[payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)],
                headers={"content-length": str(len(payload))},
            )
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    importer = ReferenceImporter(_make_config(), http_client=http, storage_client=storage, callback=callback)

    importer.run()

    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert blob.chunk_size is not None, "chunk_size must be set so the upload is chunked / resumable"
    # GCS resumable chunk_size must be a multiple of 256 KiB; assert that
    # and a sane minimum so a single chunk doesn't try to buffer many MB.
    assert blob.chunk_size % (256 * 1024) == 0
    assert 256 * 1024 <= blob.chunk_size <= 32 * 1024 * 1024


def test_extract_tar_gz_writes_each_member_as_its_own_blob():
    """extract_mode='tar.gz': same as tar but the archive is gzipped first."""
    import io
    import tarfile

    from app.workers.reference_importer import ReferenceImporter

    members = {"a.txt": b"aa" * 5000, "b.txt": b"bb" * 5000}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    archive_bytes = buf.getvalue()

    url = "https://ftp.example.org/data/refs/pack.tar.gz"
    http = _FakeHttpClient(
        {
            url: _FakeResponse(
                status_code=200,
                chunks=[archive_bytes[i : i + 64 * 1024] for i in range(0, len(archive_bytes), 64 * 1024)],
                headers={"content-length": str(len(archive_bytes))},
            ),
        }
    )
    storage = _FakeStorageClient()
    callback = _RecordingCallback()
    config = _make_config(source_url=url, extract_mode="tar.gz")
    result = ReferenceImporter(config, http_client=http, storage_client=storage, callback=callback).run()

    bucket_blobs = storage.blobs["bioaf-references-test"]
    assert sorted(bucket_blobs.keys()) == [
        "annotation/gencode/v45/a.txt",
        "annotation/gencode/v45/b.txt",
    ]
    assert sorted(f.filename for f in result.files) == ["a.txt", "b.txt"]
    assert [e["status"] for e in callback.events][-1] == "active"
