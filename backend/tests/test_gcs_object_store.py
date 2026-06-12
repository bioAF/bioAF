"""Tests for the GCS adapter object-store operations (Phase 3).

Two modes are exercised:

* **local mode** (BIOAF_COMPUTE_MODE=local): object ops emulate an object store
  on the local filesystem under LOCAL_DATA_ROOT, so dev/CI need no real GCS.
* **GCS mode**: object ops go through the google-cloud-storage client, which is
  mocked here via the patched ``_get_gcs_client`` factory.

Both modes share the same URI-first contract: every op takes a ``gs://...`` URI
(or a StorageStore + key for ``resolve_uri``).
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import ValidationError

from app.adapters.models import ObjectMetadata, StorageStore, StorageObjectNotFound
from app.adapters.storage.gcs import GcsStorageProvider


@pytest.fixture
def local_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import gcs

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path))
    return GcsStorageProvider(org_slug="testorg")


@pytest.fixture
def gcs_adapter(monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    adapter = GcsStorageProvider(org_slug="testorg")
    # Credentials resolution hits the DB; stub it for unit tests.
    monkeypatch.setattr(adapter, "_get_credentials", _async_none)
    return adapter


async def _async_none(*args, **kwargs):
    return None


# --- resolve_uri -------------------------------------------------------------


class TestResolveUri:
    @pytest.mark.asyncio
    async def test_local_uses_org_slug_buckets(self, local_adapter):
        uri = await local_adapter.resolve_uri(StorageStore.INGEST, "uploads/x.fastq")
        assert uri == "gs://bioaf-ingest-testorg/uploads/x.fastq"

    @pytest.mark.asyncio
    async def test_local_strips_leading_slash_on_key(self, local_adapter):
        uri = await local_adapter.resolve_uri(StorageStore.RESULTS, "/a/b.txt")
        assert uri == "gs://bioaf-results-testorg/a/b.txt"

    @pytest.mark.asyncio
    async def test_gcs_reads_bucket_name_from_config(self, gcs_adapter, monkeypatch):
        async def fake_config():
            return {"references_bucket_name": "my-refs-bucket"}

        monkeypatch.setattr(gcs_adapter, "_get_bucket_config", fake_config)
        uri = await gcs_adapter.resolve_uri(StorageStore.REFERENCES, "panel.csv")
        assert uri == "gs://my-refs-bucket/panel.csv"


# --- build_uri / parse_uri (explicit-bucket scheme minting) ------------------
#
# resolve_uri resolves a logical StorageStore to its configured bucket. build_uri
# is its scheme-neutral counterpart for when the bucket is a runtime value (a
# backup bucket, an event's source bucket): it lets callers stop hardcoding the
# gs:// scheme. Pure string transforms, so they are sync (no DB/credentials).


class TestBuildAndParseUri:
    def test_build_uri_mints_gs_scheme(self, local_adapter):
        assert local_adapter.build_uri("my-bucket", "a/b.txt") == "gs://my-bucket/a/b.txt"

    def test_build_uri_strips_leading_slash_on_key(self, local_adapter):
        # Mirrors resolve_uri's normalization so the two mint identical URIs.
        assert local_adapter.build_uri("my-bucket", "/a/b.txt") == "gs://my-bucket/a/b.txt"

    def test_parse_uri_round_trips_build_uri(self, local_adapter):
        uri = local_adapter.build_uri("my-bucket", "deep/a/b.txt")
        assert local_adapter.parse_uri(uri) == ("my-bucket", "deep/a/b.txt")

    def test_parse_uri_rejects_non_gs_scheme(self, local_adapter):
        with pytest.raises(ValidationError):
            local_adapter.parse_uri("s3://my-bucket/a/b.txt")


# --- read / write round-trips (local) ----------------------------------------


class TestLocalReadWrite:
    @pytest.mark.asyncio
    async def test_write_then_read_text(self, local_adapter):
        uri = "gs://bioaf-working-testorg/notes/a.txt"
        await local_adapter.write_text(uri, "hello world")
        assert await local_adapter.read_text(uri) == "hello world"

    @pytest.mark.asyncio
    async def test_write_then_read_bytes(self, local_adapter):
        uri = "gs://bioaf-working-testorg/blob.bin"
        await local_adapter.write_bytes(uri, b"\x00\x01\x02")
        assert await local_adapter.read_bytes(uri) == b"\x00\x01\x02"

    @pytest.mark.asyncio
    async def test_read_missing_raises_not_found(self, local_adapter):
        with pytest.raises(StorageObjectNotFound):
            await local_adapter.read_text("gs://bioaf-working-testorg/nope.txt")

    @pytest.mark.asyncio
    async def test_exists(self, local_adapter):
        uri = "gs://bioaf-working-testorg/here.txt"
        assert await local_adapter.exists(uri) is False
        await local_adapter.write_text(uri, "x")
        assert await local_adapter.exists(uri) is True

    @pytest.mark.asyncio
    async def test_delete_is_idempotent(self, local_adapter):
        uri = "gs://bioaf-working-testorg/del.txt"
        await local_adapter.write_text(uri, "x")
        await local_adapter.delete(uri)
        assert await local_adapter.exists(uri) is False
        # deleting again does not raise
        await local_adapter.delete(uri)


# --- upload / download (local) -----------------------------------------------


class TestLocalUploadDownload:
    @pytest.mark.asyncio
    async def test_upload_file_streams(self, local_adapter):
        uri = "gs://bioaf-working-testorg/up.bin"
        await local_adapter.upload_file(uri, io.BytesIO(b"streamed"))
        assert await local_adapter.read_bytes(uri) == b"streamed"

    @pytest.mark.asyncio
    async def test_upload_and_download_filename(self, local_adapter, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("from disk")
        uri = "gs://bioaf-working-testorg/disk.txt"
        await local_adapter.upload_filename(uri, str(src))

        dst = tmp_path / "dst.txt"
        await local_adapter.download_to_filename(uri, str(dst))
        assert dst.read_text() == "from disk"

    @pytest.mark.asyncio
    async def test_download_to_file(self, local_adapter):
        uri = "gs://bioaf-working-testorg/d.bin"
        await local_adapter.write_bytes(uri, b"abcdef")
        buf = io.BytesIO()
        await local_adapter.download_to_file(uri, buf)
        assert buf.getvalue() == b"abcdef"


# --- list / metadata (local) -------------------------------------------------


class TestLocalListMetadata:
    @pytest.mark.asyncio
    async def test_list_objects_under_prefix(self, local_adapter):
        await local_adapter.write_text("gs://bioaf-results-testorg/exp/1.txt", "a")
        await local_adapter.write_text("gs://bioaf-results-testorg/exp/2.txt", "bb")
        await local_adapter.write_text("gs://bioaf-results-testorg/other/3.txt", "ccc")

        objs = await local_adapter.list_objects("gs://bioaf-results-testorg/exp/")
        uris = sorted(o.storage_uri for o in objs)
        assert uris == [
            "gs://bioaf-results-testorg/exp/1.txt",
            "gs://bioaf-results-testorg/exp/2.txt",
        ]

    @pytest.mark.asyncio
    async def test_get_object_metadata(self, local_adapter):
        uri = "gs://bioaf-results-testorg/m.txt"
        await local_adapter.write_text(uri, "12345")
        md = await local_adapter.get_object_metadata(uri)
        assert isinstance(md, ObjectMetadata)
        assert md.uri == uri
        assert md.size_bytes == 5

    @pytest.mark.asyncio
    async def test_metadata_missing_raises(self, local_adapter):
        with pytest.raises(StorageObjectNotFound):
            await local_adapter.get_object_metadata("gs://bioaf-results-testorg/x.txt")


# --- copy / move (local) -----------------------------------------------------


class TestLocalCopyMove:
    @pytest.mark.asyncio
    async def test_copy_keeps_source(self, local_adapter):
        src = "gs://bioaf-working-testorg/c-src.txt"
        dst = "gs://bioaf-results-testorg/c-dst.txt"
        await local_adapter.write_text(src, "payload")
        ret = await local_adapter.copy(src, dst)
        assert ret == dst
        assert await local_adapter.read_text(dst) == "payload"
        assert await local_adapter.exists(src) is True

    @pytest.mark.asyncio
    async def test_move_deletes_source(self, local_adapter):
        src = "gs://bioaf-working-testorg/m-src.txt"
        dst = "gs://bioaf-results-testorg/m-dst.txt"
        await local_adapter.write_text(src, "payload")
        ret = await local_adapter.move(src, dst)
        assert ret == dst
        assert await local_adapter.read_text(dst) == "payload"
        assert await local_adapter.exists(src) is False


# --- GCS mode (mocked client) ------------------------------------------------


def _mock_client_with_blob(blob):
    client = MagicMock()
    bucket = MagicMock()
    client.bucket.return_value = bucket
    bucket.blob.return_value = blob
    return client, bucket


class TestGcsModeOps:
    @pytest.mark.asyncio
    async def test_read_text_downloads(self, gcs_adapter):
        blob = MagicMock()
        blob.download_as_bytes.return_value = b"remote text"
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            assert await gcs_adapter.read_text("gs://b/k.txt") == "remote text"
        client.bucket.assert_called_once_with("b")

    @pytest.mark.asyncio
    async def test_read_missing_raises_not_found(self, gcs_adapter):
        from google.api_core.exceptions import NotFound

        blob = MagicMock()
        blob.download_as_bytes.side_effect = NotFound("gone")
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            with pytest.raises(StorageObjectNotFound):
                await gcs_adapter.read_bytes("gs://b/missing")

    @pytest.mark.asyncio
    async def test_write_text_uploads(self, gcs_adapter):
        blob = MagicMock()
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            await gcs_adapter.write_text("gs://b/k.txt", "data", content_type="text/csv")
        blob.upload_from_string.assert_called_once()
        _, kwargs = blob.upload_from_string.call_args
        assert kwargs.get("content_type") == "text/csv"

    @pytest.mark.asyncio
    async def test_upload_file_streams_through_blob(self, gcs_adapter):
        blob = MagicMock()
        client, _ = _mock_client_with_blob(blob)
        fobj = io.BytesIO(b"x")
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            await gcs_adapter.upload_file("gs://b/k", fobj)
        blob.upload_from_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete(self, gcs_adapter):
        blob = MagicMock()
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            await gcs_adapter.delete("gs://b/k")
        blob.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_with_generation(self, gcs_adapter):
        """A specific object generation can be deleted (noncurrent-version wipe)."""
        blob = MagicMock()
        client = MagicMock()
        bucket = MagicMock()
        client.bucket.return_value = bucket
        bucket.blob.return_value = blob
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            await gcs_adapter.delete("gs://b/k", generation=42)
        bucket.blob.assert_called_once_with("k", generation=42)
        blob.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_objects_includes_generation_with_versions(self, gcs_adapter):
        b1 = MagicMock()
        b1.name = "k"
        b1.size = 1
        b1.md5_hash = "h"
        b1.generation = 99
        client = MagicMock()
        bucket = MagicMock()
        client.bucket.return_value = bucket
        bucket.list_blobs.return_value = [b1]
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            objs = await gcs_adapter.list_objects("gs://b/", include_versions=True)
        assert objs[0].provider_details.get("generation") == 99
        assert bucket.list_blobs.call_args.kwargs.get("versions") is True

    @pytest.mark.asyncio
    async def test_get_bucket_info_versioning(self, gcs_adapter):
        bucket = MagicMock()
        bucket.versioning_enabled = True
        client = MagicMock()
        client.get_bucket.return_value = bucket
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            info = await gcs_adapter.get_bucket_info("gs://b/some/key")
        assert info["versioning_enabled"] is True
        client.get_bucket.assert_called_once_with("b")

    @pytest.mark.asyncio
    async def test_exists(self, gcs_adapter):
        blob = MagicMock()
        blob.exists.return_value = True
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            assert await gcs_adapter.exists("gs://b/k") is True

    @pytest.mark.asyncio
    async def test_list_objects(self, gcs_adapter):
        b1 = MagicMock(name="exp/1.txt")
        b1.name = "exp/1.txt"
        b1.size = 10
        b1.md5_hash = "h1"
        b2 = MagicMock()
        b2.name = "exp/2.txt"
        b2.size = 20
        b2.md5_hash = "h2"
        client = MagicMock()
        bucket = MagicMock()
        client.bucket.return_value = bucket
        bucket.list_blobs.return_value = [b1, b2]
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            objs = await gcs_adapter.list_objects("gs://b/exp/")
        assert [o.storage_uri for o in objs] == ["gs://b/exp/1.txt", "gs://b/exp/2.txt"]
        assert objs[0].size_bytes == 10
        bucket.list_blobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_object_metadata(self, gcs_adapter):
        blob = MagicMock()
        blob.exists.return_value = True
        blob.size = 123
        blob.md5_hash = "abc"
        blob.content_type = "application/json"
        blob.storage_class = "STANDARD"
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            md = await gcs_adapter.get_object_metadata("gs://b/k")
        assert md.size_bytes == 123
        assert md.md5_hash == "abc"
        assert md.content_type == "application/json"

    @pytest.mark.asyncio
    async def test_move_is_failsafe_on_partial_failure(self, gcs_adapter):
        """If copy verification fails, source must NOT be deleted."""
        src_blob = MagicMock()
        dst_blob = MagicMock()
        dst_blob.exists.return_value = False  # copy "failed" to land

        client = MagicMock()
        src_bucket = MagicMock()
        dst_bucket = MagicMock()
        client.bucket.side_effect = lambda name: src_bucket if name == "src" else dst_bucket
        src_bucket.blob.return_value = src_blob
        dst_bucket.blob.return_value = dst_blob

        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            with pytest.raises(RuntimeError):
                await gcs_adapter.move("gs://src/a", "gs://dst/b")
        src_blob.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_deletes_source_after_verified_copy(self, gcs_adapter):
        src_blob = MagicMock()
        dst_blob = MagicMock()
        dst_blob.exists.return_value = True

        client = MagicMock()
        src_bucket = MagicMock()
        dst_bucket = MagicMock()
        client.bucket.side_effect = lambda name: src_bucket if name == "src" else dst_bucket
        src_bucket.blob.return_value = src_blob
        dst_bucket.blob.return_value = dst_blob

        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            ret = await gcs_adapter.move("gs://src/a", "gs://dst/b")
        assert ret == "gs://dst/b"
        dst_bucket.copy_blob.assert_called_once()
        src_blob.delete.assert_called_once()


# --- signed URL + capability gating ------------------------------------------


class TestSignedUrl:
    @pytest.mark.asyncio
    async def test_generate_signed_url_gcs(self, gcs_adapter):
        blob = MagicMock()
        blob.generate_signed_url.return_value = "https://signed.example/url"
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            url = await gcs_adapter.generate_signed_url("gs://b/k", method="PUT", expiry_seconds=900)
        assert url == "https://signed.example/url"
        _, kwargs = blob.generate_signed_url.call_args
        assert kwargs.get("method") == "PUT"

    def test_gcs_declares_signed_url_capability(self, gcs_adapter):
        assert gcs_adapter.capabilities().signed_url_upload is True

    @pytest.mark.asyncio
    async def test_create_resumable_upload_url(self, gcs_adapter):
        blob = MagicMock()
        blob.create_resumable_upload_session.return_value = "https://resumable.example/session"
        client, _ = _mock_client_with_blob(blob)
        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            url = await gcs_adapter.create_resumable_upload_url(
                "gs://b/k", content_type="application/zip", size_bytes=123, origin="https://app.example"
            )
        assert url == "https://resumable.example/session"
        _, kwargs = blob.create_resumable_upload_session.call_args
        assert kwargs == {"content_type": "application/zip", "size": 123, "origin": "https://app.example"}


# --- storage metrics self-contained (inversion reversal) ---------------------


class TestGcsStorageMetricsSelfContained:
    @pytest.mark.asyncio
    async def test_metrics_enumerate_configured_buckets(self, gcs_adapter, monkeypatch):
        async def fake_storage_config():
            return {
                "storage_deployed": "true",
                "raw_bucket_name": "bioaf-raw",
                "results_bucket_name": "bioaf-results",
            }

        monkeypatch.setattr(gcs_adapter, "_read_storage_config", fake_storage_config)

        def make_blob(size):
            b = MagicMock()
            b.size = size
            return b

        client = MagicMock()
        bucket = MagicMock()
        bucket.storage_class = "STANDARD"
        client.get_bucket.return_value = bucket
        client.list_blobs.return_value = [make_blob(1024**3), make_blob(1024**3)]  # 2 GiB

        with patch.object(gcs_adapter, "_get_gcs_client", return_value=client):
            result = await gcs_adapter._gcs_storage_metrics()

        names = sorted(b["name"] for b in result["buckets"])
        assert names == ["bioaf-raw", "bioaf-results"]
        assert all(b["size_gb"] == 2.0 for b in result["buckets"])
        assert all(b["object_count"] == 2 for b in result["buckets"])

    @pytest.mark.asyncio
    async def test_metrics_raises_when_not_deployed(self, gcs_adapter, monkeypatch):
        async def fake_storage_config():
            return {"storage_deployed": "false"}

        monkeypatch.setattr(gcs_adapter, "_read_storage_config", fake_storage_config)
        with pytest.raises(ValidationError):
            await gcs_adapter._gcs_storage_metrics()

    def test_adapter_does_not_import_gcs_storage_service(self):
        """The inversion is reversed: the adapter module must not import the
        service it used to delegate to."""
        import app.adapters.storage.gcs as mod

        src = open(mod.__file__).read()
        assert "from app.services.gcs_storage import" not in src
        assert "import app.services.gcs_storage" not in src
