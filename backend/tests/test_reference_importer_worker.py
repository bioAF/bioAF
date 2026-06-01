"""TDD: backend/app/workers/reference_importer.py — the GKE Job entrypoint.

The importer runs inside a Pod on bioaf-cluster, reads its inputs from env
vars, streams the source URL into GCS, optionally verifies MD5 and extracts,
and POSTs progress updates back to the backend via the internal callback
endpoint. See local/specs/reference-url-import.md.

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

    @property
    def gcs_uri(self) -> str:
        return f"gs://{self.bucket_name}/{self.name}"

    def upload_from_file(self, fileobj, *, content_type=None, rewind=False, size=None):
        if rewind:
            fileobj.seek(0)
        self.content_type = content_type
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
        callback_url="http://backend/api/internal/references/42/import-progress",
        internal_token="t0ken",
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
    bytes_seen = [e.get("bytes_downloaded") for e in callback.events if e.get("bytes_downloaded") is not None]
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


def test_reports_failed_when_md5_mismatches_and_purges_blob():
    """If the upstream md5 file disagrees with the streamed payload, the
    importer reports failure with a descriptive error_message AND deletes
    the partial GCS object so the dataset prefix is clean for a retry."""
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

    # Partial blob purged.
    blob = storage.blobs["bioaf-references-test"]["annotation/gencode/v45/gencode.v45.gtf.gz"]
    assert blob.deleted is True


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
