"""Provider upload sessions bioAF can inspect and cancel (storage lifecycle 11F/11G, review #2).

A direct upload uses a provider session instead of a presigned PUT: a GCS resumable session or an
S3 multipart upload. bioAF can ask the provider whether the session is still accepting bytes,
finished, or ended, and can cancel it; a cancellation counts only once the provider confirms it.
A completion that raced the cancellation is reported as completed so the caller can clean up.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.adapters.capabilities import CapabilityNotSupported
from app.adapters.models import UploadSessionState, UploadedPart

URI = "gs://bioaf-ingest-test/uploads/u1/a.fastq.gz"
SESSION = "https://storage.googleapis.com/upload/storage/v1/b/x/o?uploadType=resumable&upload_id=SECRET"


# ---------------------------------------------------------------------------
# GCS resumable sessions
# ---------------------------------------------------------------------------


def _gcs(responses, *, exists=False):
    from app.adapters.storage.gcs import GcsStorageProvider

    adapter = GcsStorageProvider.__new__(GcsStorageProvider)
    calls = []

    def request(method, url, headers):
        calls.append((method, url, headers))
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    patches = [
        patch.object(GcsStorageProvider, "is_local", property(lambda self: False)),
        patch.object(GcsStorageProvider, "_get_credentials", return_value=None),
        patch.object(GcsStorageProvider, "_gcs_session_request", side_effect=request, create=True),
        patch.object(GcsStorageProvider, "_gcs_exists", return_value=exists),
        patch.object(GcsStorageProvider, "_gcs_create_resumable_upload_url", return_value=SESSION),
    ]
    return adapter, calls, patches


async def _with(patches, coro_fn):
    for p in patches:
        p.start()
    try:
        return await coro_fn()
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.mark.asyncio
async def test_gcs_begin_returns_a_resumable_session_for_the_browser():
    adapter, _, patches = _gcs([])
    grant = await _with(patches, lambda: adapter.begin_upload_session(URI, size_bytes=10, origin="https://lab.example"))
    assert (grant.protocol, grant.handle, grant.url, grant.part_size) == ("gcs_resumable", SESSION, SESSION, None)


@pytest.mark.parametrize(
    "status, headers, state, received",
    [
        (308, {"Range": "bytes=0-1048575"}, UploadSessionState.ACTIVE, 1048576),
        (308, {}, UploadSessionState.ACTIVE, 0),
        (200, {}, UploadSessionState.COMPLETED, None),
        (201, {}, UploadSessionState.COMPLETED, None),
        (404, {}, UploadSessionState.ENDED, None),
        (410, {}, UploadSessionState.ENDED, None),
        (499, {}, UploadSessionState.ENDED, None),
        (503, {}, UploadSessionState.UNKNOWN, None),
    ],
)
@pytest.mark.asyncio
async def test_gcs_inspect_reports_what_the_provider_says(status, headers, state, received):
    adapter, calls, patches = _gcs([(status, headers)])
    result = await _with(patches, lambda: adapter.inspect_upload_session(URI, SESSION))
    assert (result.state, result.received_bytes) == (state, received)
    method, url, sent = calls[0]
    assert (method, url, sent["Content-Range"]) == ("PUT", SESSION, "bytes */*")


@pytest.mark.asyncio
async def test_gcs_a_session_that_ended_with_its_object_stored_completed():
    adapter, _, patches = _gcs([(404, {})], exists=True)
    result = await _with(patches, lambda: adapter.inspect_upload_session(URI, SESSION))
    assert result.state is UploadSessionState.COMPLETED


@pytest.mark.asyncio
async def test_gcs_a_network_failure_is_unknown_not_ended():
    adapter, _, patches = _gcs([ConnectionError("reset")])
    result = await _with(patches, lambda: adapter.inspect_upload_session(URI, SESSION))
    assert result.state is UploadSessionState.UNKNOWN


@pytest.mark.parametrize(
    "status, exists, state",
    [
        (499, False, UploadSessionState.ENDED),  # the provider confirmed the cancellation
        (499, True, UploadSessionState.COMPLETED),  # the last bytes landed first
        (404, False, UploadSessionState.ENDED),
        (200, True, UploadSessionState.COMPLETED),
        (503, False, UploadSessionState.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_gcs_cancel_is_confirmed_by_the_provider(status, exists, state):
    adapter, calls, patches = _gcs([(status, {})], exists=exists)
    result = await _with(patches, lambda: adapter.cancel_upload_session(URI, SESSION))
    assert result.state is state
    assert calls[0][:2] == ("DELETE", SESSION)


@pytest.mark.asyncio
async def test_gcs_has_no_parts_to_sign():
    adapter, _, patches = _gcs([])
    with pytest.raises(CapabilityNotSupported):
        await _with(patches, lambda: adapter.sign_upload_parts(URI, SESSION, [1]))


# ---------------------------------------------------------------------------
# S3 multipart uploads
# ---------------------------------------------------------------------------

S3_URI = "s3://bioaf-ingest-test/uploads/u1/a.fastq.gz"


class FakeS3:
    """A boto3-shaped multipart client with scripted answers."""

    def __init__(self, *, parts=None, exists=False, parts_after_abort=None):
        self.parts = parts
        self.exists = exists
        self.parts_after_abort = parts_after_abort
        self.aborted = False
        self.completed = None
        self.created = None

    def _no_such_upload(self):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")

    def create_multipart_upload(self, **kwargs):
        self.created = kwargs
        return {"UploadId": "UPLOAD-1"}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://s3.example/{operation}?part={Params['PartNumber']}&id={Params['UploadId']}"

    def complete_multipart_upload(self, **kwargs):
        self.completed = kwargs
        return {}

    def list_parts(self, **kwargs):
        parts = self.parts_after_abort if self.aborted else self.parts
        if parts is None:
            raise self._no_such_upload()
        return {"Parts": [{"PartNumber": i + 1, "Size": size} for i, size in enumerate(parts)]}

    def abort_multipart_upload(self, **kwargs):
        self.aborted = True
        return {}

    def head_object(self, **kwargs):
        if not self.exists:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return {}


def _s3(client):
    from app.adapters.storage.s3 import S3StorageProvider

    adapter = S3StorageProvider.__new__(S3StorageProvider)
    patches = [
        patch.object(S3StorageProvider, "is_local", property(lambda self: False)),
        patch.object(S3StorageProvider, "_get_credentials", return_value=None),
        patch.object(S3StorageProvider, "_get_s3_client", return_value=client),
    ]
    return adapter, patches


@pytest.mark.parametrize(
    "size, part_size",
    [
        (None, 64 * 1024 * 1024),
        (10, 8 * 1024 * 1024),
        (10_000 * 8 * 1024 * 1024 + 1, 9 * 1024 * 1024),  # at most 10,000 parts
    ],
)
@pytest.mark.asyncio
async def test_s3_begin_starts_a_multipart_upload(size, part_size):
    client = FakeS3()
    adapter, patches = _s3(client)
    grant = await _with(patches, lambda: adapter.begin_upload_session(S3_URI, size_bytes=size))
    assert (grant.protocol, grant.handle, grant.url, grant.part_size) == ("s3_multipart", "UPLOAD-1", None, part_size)
    assert client.created is not None
    assert client.created["Bucket"] == "bioaf-ingest-test"
    assert client.created["Key"] == "uploads/u1/a.fastq.gz"


@pytest.mark.asyncio
async def test_s3_signs_each_requested_part():
    adapter, patches = _s3(FakeS3())
    urls = await _with(patches, lambda: adapter.sign_upload_parts(S3_URI, "UPLOAD-1", [1, 2]))
    assert urls == {
        1: "https://s3.example/upload_part?part=1&id=UPLOAD-1",
        2: "https://s3.example/upload_part?part=2&id=UPLOAD-1",
    }


@pytest.mark.asyncio
async def test_s3_finish_completes_with_the_parts_in_order():
    client = FakeS3()
    adapter, patches = _s3(client)
    parts = [UploadedPart(number=2, etag='"b"'), UploadedPart(number=1, etag='"a"')]
    await _with(patches, lambda: adapter.finish_upload_session(S3_URI, "UPLOAD-1", parts))
    assert client.completed is not None
    assert client.completed["MultipartUpload"] == {
        "Parts": [{"PartNumber": 1, "ETag": '"a"'}, {"PartNumber": 2, "ETag": '"b"'}]
    }


@pytest.mark.parametrize(
    "client, state, received",
    [
        (FakeS3(parts=[5, 7]), UploadSessionState.ACTIVE, 12),
        (FakeS3(parts=None, exists=True), UploadSessionState.COMPLETED, None),
        (FakeS3(parts=None, exists=False), UploadSessionState.ENDED, None),
    ],
)
@pytest.mark.asyncio
async def test_s3_inspect_reports_what_the_provider_says(client, state, received):
    adapter, patches = _s3(client)
    result = await _with(patches, lambda: adapter.inspect_upload_session(S3_URI, "UPLOAD-1"))
    assert (result.state, result.received_bytes) == (state, received)


@pytest.mark.parametrize(
    "client, state",
    [
        (FakeS3(parts=[5], parts_after_abort=None), UploadSessionState.ENDED),
        # A part still in flight when the abort landed: not confirmed yet, abort again later.
        (FakeS3(parts=[5], parts_after_abort=[5]), UploadSessionState.ACTIVE),
        (FakeS3(parts=None, exists=True), UploadSessionState.COMPLETED),
    ],
)
@pytest.mark.asyncio
async def test_s3_cancel_aborts_and_confirms_nothing_remains(client, state):
    adapter, patches = _s3(client)
    result = await _with(patches, lambda: adapter.cancel_upload_session(S3_URI, "UPLOAD-1"))
    assert result.state is state


# ---------------------------------------------------------------------------
# backends without direct client uploads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nfs_has_no_upload_sessions():
    from app.adapters.storage.nfs import NfsStorageProvider

    adapter = NfsStorageProvider.__new__(NfsStorageProvider)
    with pytest.raises(CapabilityNotSupported):
        await adapter.begin_upload_session("file:///x", size_bytes=1)


class PagedS3(FakeS3):
    """ListParts answers 1,000 parts per page, as AWS does."""

    def list_parts(self, **kwargs):
        if self.aborted:
            return super().list_parts(**kwargs)
        if kwargs.get("PartNumberMarker") == 1000:
            return {"Parts": [{"PartNumber": 1001, "Size": 8}], "IsTruncated": False}
        return {
            "Parts": [{"PartNumber": i, "Size": 8} for i in range(1, 1001)],
            "IsTruncated": True,
            "NextPartNumberMarker": 1000,
        }


@pytest.mark.asyncio
async def test_s3_progress_counts_every_page_of_parts():
    adapter, patches = _s3(PagedS3())
    result = await _with(patches, lambda: adapter.inspect_upload_session(S3_URI, "UPLOAD-1"))
    assert (result.state, result.received_bytes) == (UploadSessionState.ACTIVE, 8008)
