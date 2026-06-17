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
from unittest.mock import AsyncMock, MagicMock, patch

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


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_region_resolved_from_platform_config_when_env_unset(monkeypatch):
    """_get_credentials backfills the boto3 client region from platform_config
    (aws_region) when the AWS env vars are unset, so presigned URLs sign with the
    install's real region instead of boto3's us-east-1 default."""
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    adapter = S3StorageProvider(org_slug="testorg")
    assert adapter._region is None

    with (
        patch("app.database.async_session_factory", lambda: _FakeSession()),
        patch(
            "app.platform.platform_config_service.PlatformConfigService.get_many",
            new=AsyncMock(return_value={"aws_region": "us-west-1"}),
        ),
    ):
        await adapter._get_credentials()

    assert adapter._region == "us-west-1"


@pytest.mark.asyncio
async def test_region_from_env_skips_platform_config(monkeypatch):
    """When the env supplies a region, _get_credentials must not read config."""
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    adapter = S3StorageProvider(org_slug="testorg")
    assert adapter._region == "eu-west-1"

    get_many = AsyncMock(return_value={"aws_region": "us-west-1"})
    with patch("app.platform.platform_config_service.PlatformConfigService.get_many", new=get_many):
        await adapter._get_credentials()

    assert adapter._region == "eu-west-1"
    get_many.assert_not_called()


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

    def test_nextflow_scratch_directives_use_s3_workdir_with_fusion(self):
        # The pipeline-launch blocker: S3 must implement this seam (was inheriting
        # the base bare NotImplementedError). Mirrors GCS (Fusion+Wave) but with the
        # s3:// workDir passed through.
        directives = S3StorageProvider().nextflow_scratch_directives("s3://bioaf-raw-x/nextflow-work")
        assert "workDir = 's3://bioaf-raw-x/nextflow-work'" in directives
        assert "fusion.enabled = true" in directives
        assert "wave.enabled = true" in directives


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


# --- 6a.5a pipeline staging: stage_inputs + collect_outputs ------------------


class TestStageInputs:
    def test_generate_stage_commands_uses_aws_cp(self):
        adapter = S3StorageProvider(org_slug="testorg")
        cmds = adapter.generate_stage_commands([{"storage_uri": "s3://b/in.fastq", "filename": "in.fastq"}], "/work")
        assert cmds == ["aws s3 cp s3://b/in.fastq /work/in.fastq"]

    def test_generate_stage_commands_falls_back_to_gcs_uri_mirror(self):
        adapter = S3StorageProvider()
        cmds = adapter.generate_stage_commands([{"gcs_uri": "s3://b/legacy.txt", "filename": "legacy.txt"}], "/w")
        assert cmds == ["aws s3 cp s3://b/legacy.txt /w/legacy.txt"]

    @pytest.mark.asyncio
    async def test_real_stage_inputs_returns_cli_commands(self, s3_adapter):
        adapter, _ = s3_adapter
        cmds = await adapter.stage_inputs([{"storage_uri": "s3://b/in.fastq", "filename": "in.fastq"}], "/work")
        assert cmds == ["aws s3 cp s3://b/in.fastq /work/in.fastq"]

    @pytest.mark.asyncio
    async def test_local_stage_inputs_copies_and_placeholders(self, local_adapter, tmp_path):
        existing = tmp_path / "exists.fastq"
        existing.write_text("data")
        work = str(tmp_path / "work")
        records = [
            {"filename": "exists.fastq", "local_path": str(existing)},
            {"filename": "missing.fastq"},
        ]
        paths = await local_adapter.stage_inputs(records, work)
        assert len(paths) == 2
        with open(paths[0]) as f:
            assert f.read() == "data"
        with open(paths[1]) as f:
            assert "placeholder" in f.read()


class TestCollectOutputs:
    @pytest.mark.asyncio
    async def test_local_collect_outputs_returns_stored_objects(self, local_adapter, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        (work / "result.txt").write_text("out")
        objs = await local_adapter.collect_outputs(str(work), {"id": "r1", "experiment_id": "e1"})
        assert len(objs) == 1
        assert objs[0].filename == "result.txt"
        assert objs[0].storage_uri == "s3://bioaf-results-testorg/experiments/e1/pipeline-runs/r1/result.txt"
        assert objs[0].size_bytes == 3

    @pytest.mark.asyncio
    async def test_real_collect_outputs_lists_results_prefix(self, s3_adapter):
        adapter, client = s3_adapter
        page = {
            "Contents": [
                {"Key": "experiments/e1/pipeline-runs/r1/out.txt", "Size": 4, "ETag": '"x"'},
            ]
        }
        client.get_paginator.return_value.paginate.return_value = [page]
        objs = await adapter.collect_outputs("/ignored", {"id": "r1", "experiment_id": "e1"})
        assert len(objs) == 1
        assert objs[0].filename == "out.txt"
        assert objs[0].storage_uri == "s3://bioaf-results-testorg/experiments/e1/pipeline-runs/r1/out.txt"


# --- 6a.5b storage metrics ---------------------------------------------------


class TestStorageMetrics:
    @pytest.mark.asyncio
    async def test_local_storage_metrics_structure(self, local_adapter):
        metrics = await local_adapter.get_storage_metrics()
        names = {b.name for b in metrics.buckets}
        assert "bioaf-results-testorg" in names
        assert metrics.total_size_gb > 0

    @pytest.mark.asyncio
    async def test_real_storage_metrics_sums_objects(self, s3_adapter, monkeypatch):
        adapter, client = s3_adapter

        async def fake_config():
            return {"storage_deployed": "true", "raw_bucket_name": "bioaf-raw-x"}

        monkeypatch.setattr(adapter, "_read_storage_config", fake_config)
        page = {"Contents": [{"Key": "a", "Size": 1024**3}, {"Key": "b", "Size": 1024**3}]}  # 2 GiB
        client.get_paginator.return_value.paginate.return_value = [page]
        metrics = await adapter.get_storage_metrics()
        assert len(metrics.buckets) == 1
        assert metrics.buckets[0].name == "bioaf-raw-x"
        assert metrics.buckets[0].object_count == 2
        assert metrics.buckets[0].size_gb == 2.0
        assert metrics.total_size_gb == 2.0

    @pytest.mark.asyncio
    async def test_real_storage_metrics_requires_deployed(self, s3_adapter, monkeypatch):
        adapter, _ = s3_adapter

        async def fake_config():
            return {"storage_deployed": "false"}

        monkeypatch.setattr(adapter, "_read_storage_config", fake_config)
        with pytest.raises(ValidationError):
            await adapter.get_storage_metrics()


# --- 6a.6 bucket admin (real S3 only, mocked client) -------------------------


class TestBucketAdmin:
    @pytest.mark.asyncio
    async def test_get_bucket_admin_metrics(self, s3_adapter):
        adapter, client = s3_adapter
        client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "a", "Size": 100}, {"Key": "b", "Size": 200}]}
        ]
        client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        client.get_bucket_lifecycle_configuration.return_value = {
            "Rules": [
                {
                    "Status": "Enabled",
                    "Transitions": [{"Days": 30, "StorageClass": "GLACIER"}],
                    "Expiration": {"Days": 365},
                }
            ]
        }
        m = await adapter.get_bucket_admin_metrics("bioaf-raw-x")
        assert m.size_bytes == 300
        assert m.object_count == 2
        assert m.versioning_enabled is True
        assert "Transition to GLACIER after 30 days" in m.lifecycle_summaries
        assert "Delete after 365 days" in m.lifecycle_summaries

    @pytest.mark.asyncio
    async def test_lifecycle_summaries_empty_when_unconfigured(self, s3_adapter):
        from botocore.exceptions import ClientError

        adapter, client = s3_adapter
        client.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
        client.get_bucket_versioning.return_value = {}
        client.get_bucket_lifecycle_configuration.side_effect = ClientError(
            {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration"
        )
        m = await adapter.get_bucket_admin_metrics("bk")
        assert m.lifecycle_summaries == []
        assert m.versioning_enabled is False

    @pytest.mark.asyncio
    async def test_delete_bucket_purges_versions_then_deletes(self, s3_adapter):
        adapter, client = s3_adapter
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Versions": [{"Key": "a", "VersionId": "v1"}],
                "DeleteMarkers": [{"Key": "a", "VersionId": "v0"}],
            }
        ]
        await adapter.delete_bucket("bk")
        client.delete_objects.assert_called_once_with(
            Bucket="bk",
            Delete={"Objects": [{"Key": "a", "VersionId": "v1"}, {"Key": "a", "VersionId": "v0"}]},
        )
        client.delete_bucket.assert_called_once_with(Bucket="bk")

    @pytest.mark.asyncio
    async def test_delete_bucket_empty_skips_delete_objects(self, s3_adapter):
        adapter, client = s3_adapter
        client.get_paginator.return_value.paginate.return_value = [{}]
        await adapter.delete_bucket("bk")
        client.delete_objects.assert_not_called()
        client.delete_bucket.assert_called_once_with(Bucket="bk")

    @pytest.mark.asyncio
    async def test_list_lifecycle_policies_filters_by_prefix(self, s3_adapter):
        adapter, client = s3_adapter
        client.list_buckets.return_value = {"Buckets": [{"Name": "bioaf-raw-x"}, {"Name": "other"}]}
        client.get_bucket_lifecycle_configuration.return_value = {"Rules": [{"ID": "r"}]}
        out = await adapter.list_lifecycle_policies("bioaf-")
        assert [p["bucket_name"] for p in out] == ["bioaf-raw-x"]
        assert out[0]["enabled"] is True

    @pytest.mark.asyncio
    async def test_query_bucket_stats_sums_by_class(self, s3_adapter):
        adapter, client = s3_adapter
        client.list_buckets.return_value = {"Buckets": [{"Name": "bioaf-raw-x"}]}
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "a", "Size": 10, "StorageClass": "STANDARD"},
                    {"Key": "b", "Size": 5, "StorageClass": "GLACIER"},
                ]
            }
        ]
        stats = await adapter.query_bucket_stats("bioaf-")
        assert stats[0]["total_bytes"] == 15
        assert stats[0]["object_count"] == 2
        assert stats[0]["by_storage_class"] == {"STANDARD": 10, "GLACIER": 5}

    def test_native_upload_client_returns_boto_client(self, s3_adapter):
        adapter, client = s3_adapter
        assert adapter.native_upload_client() is client
