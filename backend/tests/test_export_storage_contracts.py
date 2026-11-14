"""Adapter contracts data exports rely on: upload timestamps and truthful signed-link expiry (plan 24 §5)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.adapters.capabilities import CapabilityNotSupported
from app.adapters.models import SignedDownload, StoredObject
from app.adapters.storage import s3_signing
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import StorageUnavailableError
from app.settings_contract import ComputeMode

UTC = timezone.utc
HOUR = 3600
DAY = 24 * HOUR


async def _none(*args, **kwargs):
    return None


@pytest.fixture
def gcs_real(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(GcsStorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)
    monkeypatch.setattr(adapter, "_get_credentials", _none)
    return adapter


@pytest.fixture
def s3_real(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(S3StorageProvider, mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    monkeypatch.setattr(adapter, "_get_credentials", _none)
    return adapter


# -- StoredObject.uploaded_at --------------------------------------------------------------------


def test_stored_object_uploaded_at_defaults_to_unknown():
    assert StoredObject(filename="a", storage_uri="gs://b/a").uploaded_at is None


@pytest.mark.asyncio
async def test_gcs_listing_reports_creation_time_as_uploaded_at(gcs_real):
    created = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    blob = SimpleNamespace(
        name="exports/1/x.zip", size=3, md5_hash=None, time_created=created, updated=None, generation=7
    )
    client = MagicMock()
    client.bucket.return_value.list_blobs.return_value = [blob]

    with patch.object(gcs_real, "_get_gcs_client", return_value=client):
        [obj] = await gcs_real.list_objects("gs://b/exports/")

    assert obj.uploaded_at == created
    assert obj.provider_details["time_created"] == created  # existing callers keep their field


@pytest.mark.asyncio
async def test_s3_listing_reports_last_modified_as_uploaded_at(s3_real):
    modified = datetime(2026, 11, 2, 8, 30, tzinfo=UTC)
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "exports/1/x.zip", "Size": 3, "ETag": '"e"', "LastModified": modified}]}
    ]

    with patch.object(s3_real, "_get_s3_client", return_value=client):
        [obj] = await s3_real.list_objects("s3://b/exports/")

    assert obj.uploaded_at == modified


@pytest.mark.parametrize("provider", ["gcs", "s3", "nfs"])
@pytest.mark.asyncio
async def test_local_listings_report_the_destination_write_time(tmp_path, monkeypatch, provider):
    """Local uploads stamp the destination when written, not the copied source's mtime."""
    from app.adapters.storage import gcs, s3

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path / "d"))
    monkeypatch.setattr(s3, "LOCAL_DATA_ROOT", str(tmp_path / "d"))
    adapter = {
        "gcs": lambda: GcsStorageProvider(org_slug="t", mode=ComputeMode.LOCAL),
        "s3": lambda: S3StorageProvider(mode=ComputeMode.LOCAL),
        "nfs": lambda: NfsStorageProvider(root=str(tmp_path / "nfs")),
    }[provider]()
    source = tmp_path / "old.zip"
    source.write_bytes(b"zip")
    os.utime(source, (946684800, 946684800))  # 2000-01-01
    before = datetime.now(UTC) - timedelta(seconds=2)

    uri = adapter.build_uri("config", "exports/1/x.zip")
    await adapter.upload_filename(uri, str(source))
    [obj] = await adapter.list_objects(adapter.build_uri("config", "exports/"))

    assert obj.uploaded_at is not None and obj.uploaded_at.tzinfo is not None
    assert obj.uploaded_at >= before


# -- Typed signed downloads ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gcs_signs_for_the_requested_lifetime_and_reports_no_later_expiry(gcs_real):
    blob = MagicMock()
    blob.generate_signed_url.return_value = "https://storage.googleapis.com/b/k?sig"
    client = MagicMock()
    client.bucket.return_value.blob.return_value = blob
    start = datetime.now(UTC)

    with patch.object(gcs_real, "_get_gcs_client", return_value=client):
        signed = await gcs_real.sign_download("gs://b/k", max_ttl_seconds=DAY, min_ttl_seconds=HOUR)

    assert isinstance(signed, SignedDownload)
    assert signed.url == "https://storage.googleapis.com/b/k?sig"
    assert blob.generate_signed_url.call_args.kwargs["expiration"] == DAY
    assert start + timedelta(seconds=DAY - 5) <= signed.expires_at <= datetime.now(UTC) + timedelta(seconds=DAY)


@pytest.mark.asyncio
async def test_nfs_cannot_sign_downloads(tmp_path):
    with pytest.raises(CapabilityNotSupported):
        await NfsStorageProvider(root=str(tmp_path)).sign_download(
            "file://config/x", max_ttl_seconds=DAY, min_ttl_seconds=HOUR
        )


# -- The S3 credential shim (locked botocore) ----------------------------------------------------


def _refreshable(expires_in: float, *, key="AKIAOLD", refresh=None):
    from botocore.credentials import RefreshableCredentials

    calls = []

    def refresh_using():
        calls.append(1)
        if refresh is None:
            raise RuntimeError("provider unavailable")
        return refresh()

    creds = RefreshableCredentials(
        access_key=key,
        secret_key="secret",
        token="token",
        expiry_time=datetime.now(UTC) + timedelta(seconds=expires_in),
        refresh_using=refresh_using,
        method="iam-role",
    )
    return creds, calls


def _metadata(expires_in: float, key: str) -> dict:
    return {
        "access_key": key,
        "secret_key": "secret2",
        "token": "token2",
        "expiry_time": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
    }


def _expiry(snap) -> datetime:
    assert snap.expires_at is not None
    return snap.expires_at


REQUIRED = HOUR + s3_signing.SAFETY_MARGIN_SECONDS + s3_signing.HANDOFF_SECONDS


def test_credentials_covering_the_minimum_are_used_without_a_refresh():
    creds, calls = _refreshable(6 * HOUR)

    snap = s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)

    assert calls == []
    assert snap.access_key == "AKIAOLD"
    assert abs((_expiry(snap) - datetime.now(UTC)).total_seconds() - 6 * HOUR) < 5


def test_short_credentials_are_refreshed_once_and_the_new_pair_is_used():
    creds, calls = _refreshable(20 * 60, refresh=lambda: _metadata(6 * HOUR, "AKIANEW"))

    snap = s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)

    assert calls == [1]
    assert snap.access_key == "AKIANEW" and snap.token == "token2"
    assert (_expiry(snap) - datetime.now(UTC)).total_seconds() > 5 * HOUR


def test_a_provider_returning_the_same_short_credentials_is_refused():
    creds, calls = _refreshable(20 * 60, refresh=lambda: _metadata(20 * 60, "AKIAOLD"))

    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)
    assert calls == [1]


def test_a_failed_refresh_is_refused():
    creds, calls = _refreshable(20 * 60, refresh=None)

    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)
    assert calls == [1]


def test_recurring_short_credentials_refuse_until_the_provider_rotates():
    """Unchanged short credentials are a recurring window: each attempt refuses, none retries forever."""
    offered = {"expires": 20 * 60}
    creds, calls = _refreshable(20 * 60, refresh=lambda: _metadata(offered["expires"], "AKIAROT"))

    for _ in range(3):
        with pytest.raises(StorageUnavailableError):
            s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)
    offered["expires"] = 6 * HOUR
    snap = s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)

    assert calls == [1, 1, 1, 1]
    assert (_expiry(snap) - datetime.now(UTC)).total_seconds() > 5 * HOUR


def test_static_keys_without_a_token_use_the_configured_maximum():
    from botocore.credentials import Credentials

    snap = s3_signing.signing_snapshot(Credentials("AKIASTATIC", "secret"), required_seconds=REQUIRED)

    assert snap.expires_at is None


def test_a_session_token_with_unknown_expiry_is_not_treated_as_permanent():
    from botocore.credentials import Credentials

    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(Credentials("ASIA", "secret", "token"), required_seconds=REQUIRED)


def test_missing_sdk_internals_are_refused():
    from botocore.credentials import RefreshableCredentials

    creds, _ = _refreshable(6 * HOUR)
    del creds._refresh_lock
    assert isinstance(creds, RefreshableCredentials)

    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)


def test_no_credentials_are_refused():
    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(None, required_seconds=REQUIRED)


def test_the_imds_cache_extension_never_counts_as_signing_time():
    """botocore stretches a nearly expired IMDS credential's expiry; it must not pass the minimum."""
    from botocore.utils import InstanceMetadataFetcher

    raw = (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"access_key": "AKIAIMDS", "secret_key": "s", "token": "t", "expiry_time": raw}
    InstanceMetadataFetcher()._evaluate_expiration(data)
    assert data["expiry_time"] != raw  # the locked SDK extended it
    creds, _ = _refreshable(0, refresh=lambda: data)

    with pytest.raises(StorageUnavailableError):
        s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)


@pytest.mark.asyncio
async def test_s3_signs_with_the_snapshot_and_advertises_the_credential_bound_lifetime(s3_real, monkeypatch):
    creds, _ = _refreshable(3 * HOUR, key="AKIASNAP")
    monkeypatch.setattr(s3_real, "_signing_credentials", lambda: creds)
    start = datetime.now(UTC)

    signed = await s3_real.sign_download("s3://bucket/exports/1/x.zip", max_ttl_seconds=DAY, min_ttl_seconds=HOUR)

    query = parse_qs(urlsplit(signed.url).query)
    assert query["X-Amz-Credential"][0].startswith("AKIASNAP/")
    lifetime = int(query["X-Amz-Expires"][0])
    assert 3 * HOUR - s3_signing.SAFETY_MARGIN_SECONDS - 10 <= lifetime <= 3 * HOUR - s3_signing.SAFETY_MARGIN_SECONDS
    # The advertised expiry is never later than the link's own.
    assert signed.expires_at <= start + timedelta(seconds=lifetime) + timedelta(seconds=1)
    assert signed.expires_at <= creds._expiry_time


@pytest.mark.asyncio
async def test_s3_caps_long_lived_credentials_at_the_maximum(s3_real, monkeypatch):
    creds, _ = _refreshable(40 * HOUR)
    monkeypatch.setattr(s3_real, "_signing_credentials", lambda: creds)

    signed = await s3_real.sign_download("s3://bucket/k", max_ttl_seconds=DAY, min_ttl_seconds=HOUR)

    assert parse_qs(urlsplit(signed.url).query)["X-Amz-Expires"] == [str(DAY)]


@pytest.mark.asyncio
async def test_s3_refuses_rather_than_issue_a_short_link(s3_real, monkeypatch):
    creds, _ = _refreshable(12 * 60, refresh=lambda: _metadata(12 * 60, "AKIASAME"))
    monkeypatch.setattr(s3_real, "_signing_credentials", lambda: creds)

    with pytest.raises(StorageUnavailableError) as exc:
        await s3_real.sign_download("s3://bucket/k", max_ttl_seconds=DAY, min_ttl_seconds=HOUR)
    assert "AKIA" not in str(exc.value) and "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_s3_eligibility_check_refuses_early_without_signing(s3_real, monkeypatch):
    creds, _ = _refreshable(12 * 60, refresh=lambda: _metadata(12 * 60, "AKIASAME"))
    monkeypatch.setattr(s3_real, "_signing_credentials", lambda: creds)

    with pytest.raises(StorageUnavailableError):
        await s3_real.check_signed_download(min_ttl_seconds=HOUR)


def test_snapshot_is_never_logged_or_reprs_secrets():
    creds, _ = _refreshable(6 * HOUR)
    snap = s3_signing.signing_snapshot(creds, required_seconds=REQUIRED)

    assert "secret" not in repr(snap) and "token" not in repr(snap)


def test_the_locked_botocore_version_is_the_one_the_shim_was_tested_against():
    import botocore

    assert botocore.__version__ == s3_signing.TESTED_BOTOCORE_VERSION
