"""Read-only container inspection at the storage adapter boundary (storage lifecycle 11C).

Each backend reports what the provider says about a store's container: present
(with its native incarnation evidence), missing, forbidden, unavailable, or a
limit the backend cannot check. Object absence is not container absence, and
inspection never creates anything.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.models import ContainerPresence
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.settings_contract import ComputeMode


async def _none():
    return None


@pytest.fixture
def gcs(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(GcsStorageProvider, org_slug="acme", mode=ComputeMode.KUBERNETES)
    monkeypatch.setattr(adapter, "_get_credentials", _none)
    client = MagicMock()
    monkeypatch.setattr(adapter, "_get_gcs_client", lambda creds=None: client)
    return adapter, client


@pytest.mark.asyncio
async def test_gcs_present_bucket_reports_native_incarnation(gcs):
    adapter, client = gcs
    created = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    bucket = MagicMock(project_number=123456, time_created=created, labels={"managed_by": "bioaf"})
    client.get_bucket.return_value = bucket

    seen = await adapter.inspect_container("gs://bioaf-raw-acme-abc123/")

    assert seen.presence is ContainerPresence.PRESENT
    assert seen.owner == "123456"
    assert seen.created_at == created
    assert seen.labels == {"managed_by": "bioaf"}
    client.get_bucket.assert_called_once_with("bioaf-raw-acme-abc123")


@pytest.mark.parametrize(
    "exc_name, presence",
    [
        ("NotFound", ContainerPresence.MISSING),
        ("Forbidden", ContainerPresence.FORBIDDEN),
        ("ServiceUnavailable", ContainerPresence.UNAVAILABLE),
        ("TooManyRequests", ContainerPresence.UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_gcs_provider_answers_are_classified(gcs, exc_name, presence):
    from google.api_core import exceptions

    adapter, client = gcs
    client.get_bucket.side_effect = getattr(exceptions, exc_name)("https://storage.googleapis.com/x SECRET")

    seen = await adapter.inspect_container("gs://bioaf-raw-acme-abc123/")

    assert seen.presence is presence
    assert "SECRET" not in repr(seen)


@pytest.mark.asyncio
async def test_gcs_unexpected_error_is_unavailable_not_missing(gcs):
    adapter, client = gcs
    client.get_bucket.side_effect = ConnectionError("reset")

    seen = await adapter.inspect_container("gs://bioaf-raw-acme-abc123/")

    assert seen.presence is ContainerPresence.UNAVAILABLE


@pytest.mark.asyncio
async def test_gcs_local_mode_reports_missing_without_creating(monkeypatch, tmp_path):
    from app.adapters.storage import gcs as gcs_module

    monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = GcsStorageProvider(org_slug="acme", mode=ComputeMode.LOCAL)

    seen = await adapter.inspect_container("gs://bioaf-raw-acme-abc123/")

    assert seen.presence is ContainerPresence.MISSING
    assert not (tmp_path / "_objects" / "bioaf-raw-acme-abc123").exists()

    (tmp_path / "_objects" / "bioaf-raw-acme-abc123").mkdir(parents=True)
    seen = await adapter.inspect_container("gs://bioaf-raw-acme-abc123/")
    assert seen.presence is ContainerPresence.PRESENT
    assert seen.simulated is True


@pytest.fixture
def s3(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(S3StorageProvider, org_slug="acme", mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    monkeypatch.setattr(adapter, "_get_credentials", _none)
    client = MagicMock()
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: client)
    monkeypatch.setattr(adapter, "_account_id", AsyncMock(return_value="123456789012"))
    return adapter, client


def _client_error(code: str, status: int):
    from botocore.exceptions import ClientError

    return ClientError(
        {
            "Error": {"Code": code, "Message": "https://s3.amazonaws.com SECRET"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "HeadBucket",
    )


@pytest.mark.asyncio
async def test_s3_ownership_is_checked_against_the_installation_account_without_listing_buckets(s3):
    """HeadBucket with ExpectedBucketOwner needs only the bucket permissions the installer grants;
    AWS's creation date can change (for example after a bucket policy edit), so it is not used."""
    adapter, client = s3
    client.head_bucket.return_value = {}
    client.get_bucket_tagging.return_value = {
        "TagSet": [{"Key": "managed_by", "Value": "bioaf"}, {"Key": "bioaf_marker", "Value": "9f1c2e"}]
    }

    seen = await adapter.inspect_container("s3://bioaf-raw-acme-abc123/")

    assert seen.presence is ContainerPresence.PRESENT
    assert seen.owner == "own-account"
    assert seen.created_at is None
    assert seen.labels == {"managed_by": "bioaf", "bioaf_marker": "9f1c2e"}
    client.head_bucket.assert_called_with(Bucket="bioaf-raw-acme-abc123", ExpectedBucketOwner="123456789012")
    client.list_buckets.assert_not_called()


@pytest.mark.asyncio
async def test_s3_bucket_of_another_account_is_a_known_mismatch(s3):
    adapter, client = s3

    def head(**kwargs):
        if "ExpectedBucketOwner" in kwargs:
            raise _client_error("403", 403)
        return {}

    client.head_bucket.side_effect = head
    client.get_bucket_tagging.return_value = {"TagSet": []}

    seen = await adapter.inspect_container("s3://bioaf-raw-acme-abc123/")

    assert seen.presence is ContainerPresence.PRESENT
    assert seen.owner == "other-account"


@pytest.mark.asyncio
async def test_s3_without_a_recorded_account_cannot_check_the_owner(s3, monkeypatch):
    adapter, client = s3
    monkeypatch.setattr(adapter, "_account_id", AsyncMock(return_value=None))
    client.head_bucket.return_value = {}
    client.get_bucket_tagging.return_value = {"TagSet": []}

    seen = await adapter.inspect_container("s3://bioaf-raw-acme-abc123/")

    assert (seen.presence, seen.owner) == (ContainerPresence.PRESENT, None)
    assert "owner" in seen.limits


@pytest.mark.parametrize(
    "code, status, presence",
    [
        ("404", 404, ContainerPresence.MISSING),
        ("NoSuchBucket", 404, ContainerPresence.MISSING),
        ("403", 403, ContainerPresence.FORBIDDEN),
        ("503", 503, ContainerPresence.UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_s3_provider_answers_are_classified(s3, code, status, presence):
    adapter, client = s3
    client.head_bucket.side_effect = _client_error(code, status)

    seen = await adapter.inspect_container("s3://bioaf-raw-acme-abc123/")

    assert seen.presence is presence
    assert "SECRET" not in repr(seen)


@pytest.mark.asyncio
async def test_nfs_unmounted_root_is_not_mounted_and_nothing_is_created(tmp_path):
    root = tmp_path / "nfs-root"
    adapter = NfsStorageProvider(root=str(root))

    seen = await adapter.inspect_container(adapter.build_uri("raw", ""))

    assert seen.presence is ContainerPresence.NOT_MOUNTED
    assert not root.exists()


@pytest.mark.asyncio
async def test_nfs_plain_directory_is_not_treated_as_a_mount(tmp_path):
    root = tmp_path / "nfs-root"
    (root / "raw").mkdir(parents=True)
    adapter = NfsStorageProvider(root=str(root))

    seen = await adapter.inspect_container(adapter.build_uri("raw", ""))

    assert seen.presence is ContainerPresence.NOT_MOUNTED


@pytest.mark.asyncio
async def test_nfs_mounted_root_is_present(tmp_path, monkeypatch):
    root = tmp_path / "nfs-root"
    root.mkdir()
    real_ismount = os.path.ismount
    monkeypatch.setattr(os.path, "ismount", lambda p: os.path.realpath(p) == os.path.realpath(root) or real_ismount(p))
    adapter = NfsStorageProvider(root=str(root))

    seen = await adapter.inspect_container(adapter.build_uri("raw", ""))

    assert seen.presence is ContainerPresence.PRESENT
    assert "identity" in seen.limits
    assert not (root / "raw").exists()
