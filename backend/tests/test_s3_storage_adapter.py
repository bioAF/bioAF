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

import pytest

from app.exceptions import ValidationError

from app.adapters.models import StorageStore
from app.adapters.storage.s3 import S3StorageProvider


@pytest.fixture
def local_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import s3

    monkeypatch.setattr(s3, "LOCAL_DATA_ROOT", str(tmp_path))
    return S3StorageProvider(org_slug="testorg")


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
