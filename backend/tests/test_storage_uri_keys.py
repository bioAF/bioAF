"""A storage URI's key is everything after the container, character for character.

The adapters parsed URIs with ``urllib.parse.urlparse``, which reads ``?`` as the start of a query
and ``#`` as a fragment: ``gs://b/run/q?.txt`` addressed the object ``run/q``. On blackbird a
pipeline output named ``q?.txt`` was registered under its real URI, then its signed download
returned NoSuchKey and its content endpoint a 502 (plan 17 live validation, L2). Object stores
allow both characters in a key, so the key is split off literally: no query, no fragment, no
percent-decoding.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.storage import gcs as gcs_module
from app.adapters.storage import s3 as s3_module
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError

KEYS = ["run/q?.txt", "run/a#b.txt", "run/50%25.txt", "run/x?y#z", "run/?", "run/#", "a b/it's [1].txt"]


def _providers(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path / "gcs"))
    monkeypatch.setattr(s3_module, "LOCAL_DATA_ROOT", str(tmp_path / "s3"))
    return [
        (GcsStorageProvider(org_slug="t"), "gs"),
        (S3StorageProvider(org_slug="t"), "s3"),
        (NfsStorageProvider(root=str(tmp_path / "nfs")), "file"),
    ]


@pytest.mark.parametrize("key", KEYS)
def test_the_key_is_taken_literally_on_every_backend(tmp_path, monkeypatch, key):
    for provider, scheme in _providers(tmp_path, monkeypatch):
        uri = f"{scheme}://store/{key}"
        assert provider.parse_uri(uri) == ("store", key), scheme
        assert provider.build_uri(*provider.parse_uri(uri)) == uri, scheme


@pytest.mark.parametrize("scheme,cls", [("gs", GcsStorageProvider), ("s3", S3StorageProvider)])
def test_a_container_is_still_required_and_other_schemes_refused(scheme, cls):
    provider = cls(org_slug="t")
    assert provider.parse_uri(f"{scheme}://store") == ("store", "")
    with pytest.raises(ValidationError):
        provider.parse_uri(f"other://store/{KEYS[0]}")


@pytest.mark.asyncio
async def test_objects_named_with_query_and_fragment_characters_round_trip(tmp_path, monkeypatch):
    """Two names that urlparse collapsed onto one key (`q?.txt` and `q`) stay two objects."""
    for provider, scheme in _providers(tmp_path, monkeypatch):
        await provider.write_bytes(f"{scheme}://store/run/q", b"plain")
        for key in KEYS:
            await provider.write_bytes(f"{scheme}://store/{key}", key.encode())
        for key in KEYS:
            assert await provider.read_bytes(f"{scheme}://store/{key}") == key.encode(), (scheme, key)
        assert await provider.read_bytes(f"{scheme}://store/run/q") == b"plain", scheme
        listed = {o.storage_uri for o in await provider.list_objects(f"{scheme}://store/run/")}
        assert f"{scheme}://store/run/q?.txt" in listed and f"{scheme}://store/run/q" in listed, scheme


def test_cloud_gcs_addresses_the_full_key(monkeypatch):
    provider = GcsStorageProvider(org_slug="t")
    client = MagicMock()
    monkeypatch.setattr(provider, "_get_gcs_client", lambda creds: client)
    client.bucket.return_value.blob.return_value.download_as_bytes.return_value = b"x"
    provider._gcs_read_bytes("gs://results/run/28/q?.txt", None)
    client.bucket.assert_called_with("results")
    client.bucket.return_value.blob.assert_called_with("run/28/q?.txt")
