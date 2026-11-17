"""01B.2: identity-bound native and ordered-stream MD5 digests on S3, GCS, and the filesystem stores.

``object_identity`` reports an object's size, its version identity (S3 VersionId or ETag, GCS
generation, filesystem inode/mtime), and a native MD5 only when the provider's digest is trustworthy
for the whole final object. ``stream_md5`` reads exactly that version, in order, in bounded chunks,
off the event loop, and raises ObjectChanged if the object was replaced.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import io
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.models import DIGEST_CHUNK_BYTES, ObjectChanged, ObjectIdentity, StorageObjectNotFound
from app.adapters.storage.digest import ordered_md5
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.settings_contract import ComputeMode

pytestmark = pytest.mark.asyncio

DATA = gzip.compress(bytes(range(256)) * 20 + b"@read1\nACGT\n+\nIIII\n" * 200, mtime=0)
MD5 = hashlib.md5(DATA).hexdigest()


# --- the shared ordered reader ----------------------------------------------------------------


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)
        self.requested: list[int] = []
        self.closed = False

    def read(self, n: int) -> bytes:
        self.requested.append(n)
        return self._buf.read(n)

    def close(self) -> None:
        self.closed = True


async def test_ordered_md5_reads_bounded_chunks_in_order_and_reports_progress():
    reader = _Reader(DATA)
    progress: list[int] = []

    async def on_progress(done: int) -> None:
        progress.append(done)

    digest = await ordered_md5(
        reader.read,
        reader.close,
        uri="x://a",
        version="v1",
        expected_size=len(DATA),
        chunk_size=1000,
        on_progress=on_progress,
    )
    assert digest.hex == MD5 and digest.size_bytes == len(DATA) and digest.method == "streamed"
    assert digest.version == "v1"
    assert set(reader.requested) == {1000}
    assert progress[-1] == len(DATA) and progress == sorted(progress)
    assert reader.closed


async def test_ordered_md5_refuses_a_size_that_does_not_match_the_identity():
    reader = _Reader(DATA)
    with pytest.raises(ObjectChanged):
        await ordered_md5(reader.read, reader.close, uri="x://a", version="v", expected_size=len(DATA) + 1)
    assert reader.closed


async def test_ordered_md5_closes_the_stream_on_cancellation():
    started = asyncio.Event()

    class Slow(_Reader):
        def read(self, n):
            started.set()
            import time

            time.sleep(0.05)
            return super().read(1)

    reader = Slow(DATA)
    task = asyncio.create_task(
        ordered_md5(reader.read, reader.close, uri="x://a", version="v", expected_size=len(DATA), chunk_size=1)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert reader.closed


async def test_the_default_chunk_is_8_mib():
    assert DIGEST_CHUNK_BYTES == 8 * 1024 * 1024


# --- S3 ---------------------------------------------------------------------------------------


@pytest.fixture
def s3(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(S3StorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    client = MagicMock()
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: client)

    async def _creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", _creds)
    return adapter, client


def _head(**kw):
    base = {"ContentLength": len(DATA), "ETag": f'"{MD5}"', "ServerSideEncryption": "AES256"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize(
    ("head", "native"),
    [
        (_head(), MD5),  # single-part, SSE-S3: the ETag is the MD5
        (_head(ServerSideEncryption=None), None),  # unknown encryption proves nothing
        (_head(ServerSideEncryption="aws:kms"), None),
        (_head(ServerSideEncryption="aws:kms:dsse"), None),
        (_head(SSECustomerAlgorithm="AES256"), None),
        (_head(ETag=f'"{MD5[:31]}a-3"'), None),  # multipart
        (_head(ETag='"not-hex"'), None),
        (_head(ETag=None), None),
        # Uploader-controlled metadata is never a substitute.
        (_head(ServerSideEncryption="aws:kms", Metadata={"md5": MD5, "content-md5": MD5}), None),
    ],
)
async def test_s3_native_md5_is_trusted_only_for_single_part_sse_s3(s3, head, native):
    adapter, client = s3
    client.head_object.return_value = head
    identity = await adapter.object_identity("s3://bk/k.fastq.gz")
    assert identity.native_md5 == native
    assert identity.size_bytes == len(DATA)
    # Metadata reads use the same classification.
    assert (await adapter.get_object_metadata("s3://bk/k.fastq.gz")).md5_hash == native


async def test_s3_listing_cannot_prove_an_md5(s3):
    adapter, client = s3
    page = {"Contents": [{"Key": "a/b", "Size": 3, "ETag": f'"{MD5}"'}]}
    client.get_paginator.return_value.paginate.return_value = [page]
    [obj] = await adapter.list_objects("s3://bk/a/")
    assert obj.md5_hash is None
    assert obj.provider_details["etag"] == f'"{MD5}"'


async def test_s3_identity_prefers_version_id(s3):
    adapter, client = s3
    client.head_object.return_value = _head(VersionId="v123")
    assert (await adapter.object_identity("s3://bk/k")).version == "versionid:v123"
    client.head_object.return_value = _head(VersionId="null")
    assert (await adapter.object_identity("s3://bk/k")).version == f'etag:"{MD5}"'


def _body(data: bytes):
    buf = io.BytesIO(data)
    body = MagicMock()
    body.read.side_effect = lambda n=-1: buf.read(n)
    return body


async def test_s3_stream_reads_the_exact_version_in_order(s3):
    adapter, client = s3
    body = _body(DATA)
    client.get_object.return_value = {"Body": body, "ContentLength": len(DATA)}
    identity = ObjectIdentity(uri="s3://bk/k", size_bytes=len(DATA), version="versionid:v9")
    digest = await adapter.stream_md5(identity, chunk_size=777)
    assert digest.hex == MD5 and digest.method == "streamed" and digest.version == "versionid:v9"
    client.get_object.assert_called_once_with(Bucket="bk", Key="k", VersionId="v9")
    body.close.assert_called()


async def test_s3_stream_without_versioning_is_conditional_on_the_etag(s3):
    adapter, client = s3
    client.get_object.return_value = {"Body": _body(DATA), "ContentLength": len(DATA)}
    identity = ObjectIdentity(uri="s3://bk/k", size_bytes=len(DATA), version='etag:"abc-2"')
    await adapter.stream_md5(identity)
    client.get_object.assert_called_once_with(Bucket="bk", Key="k", IfMatch='"abc-2"')


@pytest.mark.parametrize("code", ["PreconditionFailed", "412", "NoSuchVersion", "NoSuchKey"])
async def test_s3_stream_detects_a_replaced_object(s3, code):
    from botocore.exceptions import ClientError

    adapter, client = s3
    client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
    identity = ObjectIdentity(uri="s3://bk/k", size_bytes=len(DATA), version='etag:"abc"')
    with pytest.raises(ObjectChanged):
        await adapter.stream_md5(identity)


async def test_s3_managed_copy_threshold_is_the_installed_8_mib_default(s3):
    """Copies of 8 MiB or more become multipart, so their ETag is not an MD5 (plan 01B.2)."""
    from boto3.s3.transfer import TransferConfig

    adapter, client = s3
    assert TransferConfig().multipart_threshold == 8 * 1024 * 1024
    await adapter.copy("s3://src/a", "s3://dst/b")
    _, kwargs = client.copy.call_args
    assert "Config" not in kwargs  # the default threshold applies


# --- GCS --------------------------------------------------------------------------------------


@pytest.fixture
def gcs(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(GcsStorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)

    async def _creds(*a, **k):
        return None

    monkeypatch.setattr(adapter, "_get_credentials", _creds)
    return adapter


def _gcs_client(blob):
    client = MagicMock()
    bucket = MagicMock()
    client.bucket.return_value = bucket
    bucket.blob.return_value = blob
    return client, bucket


async def test_gcs_identity_converts_base64_and_binds_the_generation(gcs):
    blob = MagicMock()
    blob.exists.return_value = True
    blob.size = len(DATA)
    blob.generation = 1700000000123456
    blob.md5_hash = base64.b64encode(bytes.fromhex(MD5)).decode()
    blob.component_count = None
    client, _ = _gcs_client(blob)
    with patch.object(gcs, "_get_gcs_client", return_value=client):
        identity = await gcs.object_identity("gs://b/k")
    assert identity.native_md5 == MD5
    assert identity.version == "generation:1700000000123456"


async def test_gcs_composite_objects_have_no_native_md5(gcs):
    blob = MagicMock()
    blob.exists.return_value = True
    blob.size = len(DATA)
    blob.generation = 5
    blob.md5_hash = None
    blob.component_count = 3
    client, _ = _gcs_client(blob)
    with patch.object(gcs, "_get_gcs_client", return_value=client):
        identity = await gcs.object_identity("gs://b/k")
    assert identity.native_md5 is None


async def test_gcs_stream_reads_ordered_ranges_of_the_generation(gcs):
    blob = MagicMock()
    blob.download_as_bytes.side_effect = lambda start, end, **kw: DATA[start : end + 1]
    client, bucket = _gcs_client(blob)
    identity = ObjectIdentity(uri="gs://b/k", size_bytes=len(DATA), version="generation:42")
    with patch.object(gcs, "_get_gcs_client", return_value=client):
        digest = await gcs.stream_md5(identity, chunk_size=1000)
    assert digest.hex == MD5 and digest.version == "generation:42"
    bucket.blob.assert_called_with("k", generation=42)
    starts = [c.kwargs["start"] for c in blob.download_as_bytes.call_args_list]
    assert starts == sorted(starts) and starts[0] == 0


async def test_gcs_stream_hashes_stored_bytes_not_decoded_content(gcs):
    """An object stored with Content-Encoding: gzip is hashed as stored (its MD5 is of those bytes)."""
    import gzip

    decoded = b"ACGT" * 4000
    stored = gzip.compress(decoded)

    def download(start, end, **kw):
        # The SDK decompresses a gzip-encoded object unless asked for the raw bytes.
        return stored[start : end + 1] if kw.get("raw_download") else decoded

    blob = MagicMock()
    blob.download_as_bytes.side_effect = download
    client, _ = _gcs_client(blob)
    identity = ObjectIdentity(uri="gs://b/k", size_bytes=len(stored), version="generation:7")
    with patch.object(gcs, "_get_gcs_client", return_value=client):
        digest = await gcs.stream_md5(identity, chunk_size=16)
    assert digest.hex == hashlib.md5(stored).hexdigest() and digest.size_bytes == len(stored)


async def test_gcs_stream_detects_a_replaced_generation(gcs):
    from google.api_core.exceptions import NotFound

    blob = MagicMock()
    blob.download_as_bytes.side_effect = NotFound("gone")
    client, _ = _gcs_client(blob)
    identity = ObjectIdentity(uri="gs://b/k", size_bytes=len(DATA), version="generation:42")
    with patch.object(gcs, "_get_gcs_client", return_value=client), pytest.raises(ObjectChanged):
        await gcs.stream_md5(identity)


# --- filesystem stores (S3/GCS local mode and NFS) --------------------------------------------


@pytest.fixture(params=["s3_local", "gcs_local", "nfs"])
def fs_adapter(request, monkeypatch, tmp_path):
    if request.param == "nfs":
        return NfsStorageProvider(root=str(tmp_path)), "file://raw/run/a.fastq.gz"
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    if request.param == "s3_local":
        from app.adapters.storage import s3 as s3_module

        monkeypatch.setattr(s3_module, "LOCAL_DATA_ROOT", str(tmp_path))
        return S3StorageProvider(org_slug="t"), "s3://bioaf-raw-t/run/a.fastq.gz"
    from app.adapters.storage import gcs as gcs_module

    monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path))
    return GcsStorageProvider(org_slug="t"), "gs://bioaf-raw-t/run/a.fastq.gz"


async def test_filesystem_streams_the_stored_compressed_bytes(fs_adapter):
    adapter, uri = fs_adapter
    await adapter.write_bytes(uri, DATA)
    identity = await adapter.object_identity(uri)
    assert identity.native_md5 is None  # no provider digest: an expectation must stream
    assert identity.size_bytes == len(DATA)
    digest = await adapter.stream_md5(identity, chunk_size=4096)
    assert digest.hex == MD5  # the gzip bytes, not decompressed content


async def test_filesystem_detects_a_replaced_object(fs_adapter):
    adapter, uri = fs_adapter
    await adapter.write_bytes(uri, DATA)
    identity = await adapter.object_identity(uri)
    await adapter.delete(uri)
    await adapter.write_bytes(uri, DATA[::-1])
    with pytest.raises(ObjectChanged):
        await adapter.stream_md5(identity)


async def test_filesystem_missing_object(fs_adapter):
    adapter, uri = fs_adapter
    with pytest.raises(StorageObjectNotFound):
        await adapter.object_identity(uri)
