"""Tests for the S3 storage adapter (Stage 6a: S3StorageProvider).

Mirrors test_gcs_object_store.py. Two modes are exercised across the sub-blocks:

* **local mode** (BIOAF_COMPUTE_MODE=local): object ops emulate an object store
  on the local filesystem under LOCAL_DATA_ROOT, so dev/CI need no real S3.
* **S3 mode**: object ops go through the boto3 client, mocked here via the
  patched ``_get_s3_client`` factory (no real AWS, mirroring how the GCS tests
  patch ``_get_gcs_client``).

This file starts with the 6a.1 foundation: URI/scheme methods (s3://),
CLI-staging command strings, capabilities, and registry wiring. The object-store
ops (read/write/upload/download/list/copy/move/metadata/signed-url) land in the
later 6a sub-blocks alongside their tests.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

from app.exceptions import ValidationError

from app.adapters.models import StorageObjectNotFound, StorageStore
from app.adapters.storage.s3 import S3StorageProvider


@pytest.fixture
def local_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3

    monkeypatch.setattr(s3, "LOCAL_DATA_ROOT", str(tmp_path))
    return S3StorageProvider(org_slug="testorg")


@pytest.fixture
def s3_adapter(monkeypatch):
    """An S3-mode adapter whose boto3 client is a MagicMock (no real AWS),
    mirroring how the GCS tests patch ``_get_gcs_client``."""
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    adapter = S3StorageProvider(org_slug="testorg")
    adapter._region = "us-east-1"
    client = MagicMock()
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: client)
    return adapter, client


# --- URI / scheme methods (s3://) -------------------------------------------


class TestUri:
    def test_build_uri(self):
        adapter = S3StorageProvider(org_slug="testorg")
        assert adapter.build_uri("mybucket", "a/b.txt") == "s3://mybucket/a/b.txt"

    def test_build_uri_strips_leading_slash(self):
        assert S3StorageProvider().build_uri("b", "/a/b.txt") == "s3://b/a/b.txt"

    def test_parse_uri_roundtrip(self):
        assert S3StorageProvider().parse_uri("s3://bucket/a/b.txt") == ("bucket", "a/b.txt")

    def test_parse_uri_rejects_non_s3(self):
        with pytest.raises(ValidationError):
            S3StorageProvider().parse_uri("gs://bucket/key")


class TestResolveUriLocal:
    @pytest.mark.asyncio
    async def test_local_uses_org_slug_buckets(self, local_adapter):
        uri = await local_adapter.resolve_uri(StorageStore.INGEST, "uploads/x.fastq")
        assert uri == "s3://bioaf-ingest-testorg/uploads/x.fastq"

    @pytest.mark.asyncio
    async def test_local_strips_leading_slash_on_key(self, local_adapter):
        uri = await local_adapter.resolve_uri(StorageStore.RESULTS, "/a/b.txt")
        assert uri == "s3://bioaf-results-testorg/a/b.txt"


class TestResolveOutputPathLocalAndS3:
    @pytest.mark.asyncio
    async def test_local_output_path_under_data_root(self, local_adapter):
        path = await local_adapter.resolve_output_path({"id": "r1", "experiment_id": "e1"}, "out.txt")
        assert path.endswith("/results/experiments/e1/pipeline-runs/r1/out.txt")

    @pytest.mark.asyncio
    async def test_s3_output_path_is_results_bucket_uri(self, monkeypatch):
        monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
        adapter = S3StorageProvider(org_slug="testorg")
        path = await adapter.resolve_output_path({"id": "r1", "experiment_id": "e1"}, "out.txt")
        assert path == "s3://bioaf-results-testorg/experiments/e1/pipeline-runs/r1/out.txt"


# --- Container-side CLI staging (aws s3 / ambient auth) ----------------------


class TestCliStaging:
    def test_cli_auth_is_ambient(self):
        # S3 authenticates via instance profile / IRSA, so no explicit auth step.
        assert S3StorageProvider().cli_auth_command("/secrets/key") == ""

    def test_cli_copy_in(self):
        assert S3StorageProvider().cli_copy_in("s3://b/k", "/tmp/x") == "aws s3 cp s3://b/k /tmp/x"

    def test_cli_copy_out(self):
        assert S3StorageProvider().cli_copy_out("/tmp/x", "s3://b/k") == "aws s3 cp --recursive /tmp/x s3://b/k"

    def test_sync_in_is_tolerant(self):
        assert S3StorageProvider().sync_in_command("s3://b/p", "/local") == [
            "/bin/sh",
            "-c",
            "aws s3 sync s3://b/p /local || true",
        ]

    def test_sync_out(self):
        assert S3StorageProvider().sync_out_command("/local", "s3://b/p") == [
            "/bin/sh",
            "-c",
            "aws s3 sync /local s3://b/p",
        ]

    def test_staging_image(self):
        assert S3StorageProvider().staging_image() == "amazon/aws-cli"

    def test_pip_packages_include_boto3(self):
        assert "boto3" in S3StorageProvider().image_storage_pip_packages()


# --- Capabilities ------------------------------------------------------------


class TestCapabilities:
    def test_supports_signed_url_and_tier_metrics(self):
        caps = S3StorageProvider().capabilities()
        assert caps.signed_url_upload is True
        assert caps.storage_tier_metrics is True


# --- Registry wiring ---------------------------------------------------------


class TestRegistryWiring:
    def test_factory_constructs_s3_provider(self):
        from app.adapters.registry import _create_storage_adapter

        adapter = _create_storage_adapter("s3")
        assert isinstance(adapter, S3StorageProvider)


# --- 6a.2 core object ops: local-mode round-trips (real behavior, no boto3) --


class TestLocalObjectOps:
    @pytest.mark.asyncio
    async def test_write_then_read_bytes(self, local_adapter):
        uri = local_adapter.build_uri("bioaf-raw-testorg", "a/b.bin")
        await local_adapter.write_bytes(uri, b"hello")
        assert await local_adapter.read_bytes(uri) == b"hello"

    @pytest.mark.asyncio
    async def test_write_then_read_text_roundtrips_unicode(self, local_adapter):
        uri = local_adapter.build_uri("b", "t.txt")
        await local_adapter.write_text(uri, "héllo")
        assert await local_adapter.read_text(uri) == "héllo"

    @pytest.mark.asyncio
    async def test_read_missing_raises_not_found(self, local_adapter):
        with pytest.raises(StorageObjectNotFound):
            await local_adapter.read_bytes(local_adapter.build_uri("b", "missing"))

    @pytest.mark.asyncio
    async def test_exists_reflects_presence(self, local_adapter):
        uri = local_adapter.build_uri("b", "e.txt")
        assert await local_adapter.exists(uri) is False
        await local_adapter.write_bytes(uri, b"x")
        assert await local_adapter.exists(uri) is True

    @pytest.mark.asyncio
    async def test_delete_is_idempotent_and_removes(self, local_adapter):
        uri = local_adapter.build_uri("b", "d.txt")
        await local_adapter.delete(uri)  # missing object is not an error
        await local_adapter.write_bytes(uri, b"x")
        await local_adapter.delete(uri)
        assert await local_adapter.exists(uri) is False

    @pytest.mark.asyncio
    async def test_upload_then_download_filename(self, local_adapter, tmp_path):
        src = tmp_path / "src.txt"
        src.write_bytes(b"payload")
        uri = local_adapter.build_uri("b", "up.txt")
        await local_adapter.upload_filename(uri, str(src))
        dest = tmp_path / "nested" / "out.txt"
        await local_adapter.download_to_filename(uri, str(dest))
        assert dest.read_bytes() == b"payload"

    @pytest.mark.asyncio
    async def test_upload_then_download_fileobj(self, local_adapter):
        uri = local_adapter.build_uri("b", "fo.bin")
        await local_adapter.upload_file(uri, io.BytesIO(b"streamed"))
        buf = io.BytesIO()
        await local_adapter.download_to_file(uri, buf)
        assert buf.getvalue() == b"streamed"

    @pytest.mark.asyncio
    async def test_traversal_key_rejected(self, local_adapter):
        with pytest.raises(ValidationError):
            await local_adapter.read_bytes("s3://b/../escape")


# --- 6a.2 core object ops: S3 mode against a mocked boto3 client --------------


class TestS3ModeObjectOps:
    @pytest.mark.asyncio
    async def test_write_bytes_calls_put_object(self, s3_adapter):
        adapter, client = s3_adapter
        await adapter.write_bytes("s3://bk/a/b.bin", b"data", content_type="text/plain")
        client.put_object.assert_called_once_with(Bucket="bk", Key="a/b.bin", Body=b"data", ContentType="text/plain")

    @pytest.mark.asyncio
    async def test_read_bytes_calls_get_object(self, s3_adapter):
        adapter, client = s3_adapter
        client.get_object.return_value = {"Body": io.BytesIO(b"payload")}
        assert await adapter.read_bytes("s3://bk/k") == b"payload"
        client.get_object.assert_called_once_with(Bucket="bk", Key="k")

    @pytest.mark.asyncio
    async def test_read_bytes_missing_maps_to_not_found(self, s3_adapter):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        with pytest.raises(StorageObjectNotFound):
            await adapter.read_bytes("s3://bk/missing")

    @pytest.mark.asyncio
    async def test_exists_true_then_false_on_404(self, s3_adapter):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        assert await adapter.exists("s3://bk/here") is True
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject"
        )
        assert await adapter.exists("s3://bk/gone") is False

    @pytest.mark.asyncio
    async def test_delete_calls_delete_object(self, s3_adapter):
        adapter, client = s3_adapter
        await adapter.delete("s3://bk/k")
        client.delete_object.assert_called_once_with(Bucket="bk", Key="k")

    @pytest.mark.asyncio
    async def test_delete_with_generation_is_not_supported_yet(self, s3_adapter):
        adapter, _ = s3_adapter
        with pytest.raises(NotImplementedError):
            await adapter.delete("s3://bk/k", generation=123)

    @pytest.mark.asyncio
    async def test_upload_filename_passes_content_type(self, s3_adapter, tmp_path):
        adapter, client = s3_adapter
        src = tmp_path / "f.txt"
        src.write_text("x")
        await adapter.upload_filename("s3://bk/up.txt", str(src), content_type="text/plain")
        client.upload_file.assert_called_once_with(str(src), "bk", "up.txt", ExtraArgs={"ContentType": "text/plain"})

    @pytest.mark.asyncio
    async def test_download_to_filename_missing_maps_to_not_found(self, s3_adapter, tmp_path):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        client.download_file.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with pytest.raises(StorageObjectNotFound):
            await adapter.download_to_filename("s3://bk/missing", str(tmp_path / "x"))


# --- 6a.3 list / copy / move / metadata: local-mode round-trips --------------


class TestLocalListCopyMove:
    @pytest.mark.asyncio
    async def test_list_objects_under_prefix(self, local_adapter):
        for k in ["p/a.txt", "p/sub/b.txt", "other/c.txt"]:
            await local_adapter.write_bytes(local_adapter.build_uri("bk", k), b"x")
        objs = await local_adapter.list_objects("s3://bk/p/")
        assert sorted(o.storage_uri for o in objs) == ["s3://bk/p/a.txt", "s3://bk/p/sub/b.txt"]
        assert all(o.size_bytes == 1 for o in objs)

    @pytest.mark.asyncio
    async def test_list_empty_bucket_is_empty(self, local_adapter):
        assert await local_adapter.list_objects("s3://bk/none/") == []

    @pytest.mark.asyncio
    async def test_list_respects_max_results(self, local_adapter):
        for i in range(4):
            await local_adapter.write_bytes(local_adapter.build_uri("bk", f"p/{i}.txt"), b"x")
        assert len(await local_adapter.list_objects("s3://bk/p/", max_results=2)) == 2

    @pytest.mark.asyncio
    async def test_copy_leaves_source_and_duplicates(self, local_adapter):
        src = local_adapter.build_uri("bk", "a.txt")
        dst = local_adapter.build_uri("bk", "b.txt")
        await local_adapter.write_bytes(src, b"data")
        assert await local_adapter.copy(src, dst) == dst
        assert await local_adapter.read_bytes(dst) == b"data"
        assert await local_adapter.exists(src) is True

    @pytest.mark.asyncio
    async def test_move_removes_source(self, local_adapter):
        src = local_adapter.build_uri("bk", "a.txt")
        dst = local_adapter.build_uri("bk", "b.txt")
        await local_adapter.write_bytes(src, b"data")
        assert await local_adapter.move(src, dst) == dst
        assert await local_adapter.read_bytes(dst) == b"data"
        assert await local_adapter.exists(src) is False

    @pytest.mark.asyncio
    async def test_copy_missing_source_raises(self, local_adapter):
        with pytest.raises(StorageObjectNotFound):
            await local_adapter.copy(local_adapter.build_uri("bk", "nope"), local_adapter.build_uri("bk", "x"))

    @pytest.mark.asyncio
    async def test_metadata_size_and_missing(self, local_adapter):
        uri = local_adapter.build_uri("bk", "m.txt")
        await local_adapter.write_bytes(uri, b"12345")
        md = await local_adapter.get_object_metadata(uri)
        assert md.size_bytes == 5
        assert md.uri == uri
        with pytest.raises(StorageObjectNotFound):
            await local_adapter.get_object_metadata(local_adapter.build_uri("bk", "gone"))

    @pytest.mark.asyncio
    async def test_local_bucket_info_no_versioning(self, local_adapter):
        assert (await local_adapter.get_bucket_info("s3://bk/k"))["versioning_enabled"] is False


# --- 6a.3 list / copy / move / metadata: S3 mode against a mocked client ------


class TestS3ModeListCopyMove:
    @pytest.mark.asyncio
    async def test_list_objects_maps_contents(self, s3_adapter):
        import datetime

        adapter, client = s3_adapter
        page = {
            "Contents": [
                {
                    "Key": "a/b.txt",
                    "Size": 3,
                    "ETag": '"abc123"',
                    "LastModified": datetime.datetime(2026, 1, 1),
                    "StorageClass": "STANDARD",
                },
                {"Key": "a/c.txt", "Size": 5, "ETag": '"def456-2"'},
            ]
        }
        client.get_paginator.return_value.paginate.return_value = [page]
        objs = await adapter.list_objects("s3://bk/a/")
        assert [o.filename for o in objs] == ["b.txt", "c.txt"]
        assert objs[0].storage_uri == "s3://bk/a/b.txt"
        assert objs[0].size_bytes == 3
        assert objs[0].md5_hash == "abc123"
        assert objs[1].md5_hash is None  # multipart ETag is not an MD5
        client.get_paginator.assert_called_with("list_objects_v2")

    @pytest.mark.asyncio
    async def test_list_objects_max_results_stops_early(self, s3_adapter):
        adapter, client = s3_adapter
        page = {"Contents": [{"Key": f"k{i}", "Size": 1, "ETag": '"x"'} for i in range(5)]}
        client.get_paginator.return_value.paginate.return_value = [page]
        assert len(await adapter.list_objects("s3://bk/", max_results=2)) == 2

    @pytest.mark.asyncio
    async def test_list_versions_uses_versions_paginator(self, s3_adapter):
        adapter, client = s3_adapter
        page = {"Versions": [{"Key": "k", "Size": 1, "ETag": '"x"', "VersionId": "v1", "IsLatest": True}]}
        client.get_paginator.return_value.paginate.return_value = [page]
        objs = await adapter.list_objects("s3://bk/", include_versions=True)
        assert objs[0].provider_details["version_id"] == "v1"
        client.get_paginator.assert_called_with("list_object_versions")

    @pytest.mark.asyncio
    async def test_copy_calls_managed_copy(self, s3_adapter):
        adapter, client = s3_adapter
        assert await adapter.copy("s3://src/a.txt", "s3://dst/b.txt") == "s3://dst/b.txt"
        client.copy.assert_called_once_with({"Bucket": "src", "Key": "a.txt"}, "dst", "b.txt")

    @pytest.mark.asyncio
    async def test_move_copies_verifies_then_deletes(self, s3_adapter):
        adapter, client = s3_adapter
        await adapter.move("s3://src/a.txt", "s3://dst/b.txt")
        client.copy.assert_called_once_with({"Bucket": "src", "Key": "a.txt"}, "dst", "b.txt")
        client.head_object.assert_called_once_with(Bucket="dst", Key="b.txt")
        client.delete_object.assert_called_once_with(Bucket="src", Key="a.txt")

    @pytest.mark.asyncio
    async def test_move_aborts_and_keeps_source_if_verify_fails(self, s3_adapter):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with pytest.raises(RuntimeError):
            await adapter.move("s3://src/a.txt", "s3://dst/b.txt")
        client.delete_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_object_metadata_maps_head(self, s3_adapter):
        import datetime

        adapter, client = s3_adapter
        client.head_object.return_value = {
            "ContentLength": 7,
            "ETag": '"deadbeef"',
            "ContentType": "text/plain",
            "StorageClass": "STANDARD",
            "LastModified": datetime.datetime(2026, 1, 1),
        }
        md = await adapter.get_object_metadata("s3://bk/k")
        assert md.size_bytes == 7
        assert md.md5_hash == "deadbeef"
        assert md.content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_get_object_metadata_missing_raises(self, s3_adapter):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with pytest.raises(StorageObjectNotFound):
            await adapter.get_object_metadata("s3://bk/missing")

    @pytest.mark.asyncio
    async def test_get_bucket_info_versioning(self, s3_adapter):
        adapter, client = s3_adapter
        client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        assert (await adapter.get_bucket_info("s3://bk/k"))["versioning_enabled"] is True
        client.get_bucket_versioning.return_value = {}
        assert (await adapter.get_bucket_info("s3://bk/k"))["versioning_enabled"] is False


# --- 6a.4 signed URLs + resumable upload (presigned, S3 mode only) -----------


class TestS3SignedUrls:
    @pytest.mark.asyncio
    async def test_signed_get_url_presigns_get_object(self, s3_adapter):
        adapter, client = s3_adapter
        client.generate_presigned_url.return_value = "https://signed/get"
        url = await adapter.generate_signed_url("s3://bk/k", method="GET", expiry_seconds=120)
        assert url == "https://signed/get"
        client.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": "bk", "Key": "k"}, ExpiresIn=120
        )

    @pytest.mark.asyncio
    async def test_signed_put_url_includes_content_type(self, s3_adapter):
        adapter, client = s3_adapter
        await adapter.generate_signed_url("s3://bk/k", method="PUT", content_type="text/csv")
        client.generate_presigned_url.assert_called_once_with(
            "put_object",
            Params={"Bucket": "bk", "Key": "k", "ContentType": "text/csv"},
            ExpiresIn=3600,
        )

    @pytest.mark.asyncio
    async def test_unsupported_method_raises(self, s3_adapter):
        adapter, _ = s3_adapter
        with pytest.raises(ValidationError):
            await adapter.generate_signed_url("s3://bk/k", method="PATCH")

    @pytest.mark.asyncio
    async def test_resumable_upload_url_presigns_put(self, s3_adapter):
        adapter, client = s3_adapter
        client.generate_presigned_url.return_value = "https://signed/put"
        url = await adapter.create_resumable_upload_url("s3://bk/big.fastq", content_type="application/octet-stream")
        assert url == "https://signed/put"
        client.generate_presigned_url.assert_called_once_with(
            "put_object",
            Params={"Bucket": "bk", "Key": "big.fastq", "ContentType": "application/octet-stream"},
            ExpiresIn=3600,
        )
