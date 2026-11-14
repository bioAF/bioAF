"""Custom object metadata through the storage boundary (StorageProvider.upload_file/upload_filename ``metadata``,
ObjectMetadata.metadata).

Callers that omit ``metadata`` keep today's behavior. A caller that passes it gets the same map back from
``get_object_metadata`` on every backend: GCS custom metadata, S3 object metadata, and, for the local filesystem
store (GCS/S3 local mode and NFS), a private record outside the object tree that follows the object through
overwrite, copy, move and delete and never appears in a listing. Invalid metadata is refused before any upload.
"""

from __future__ import annotations

import io
import os
import stat
from unittest.mock import MagicMock

import pytest

from app.adapters.models import StorageObjectNotFound, validate_object_metadata
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.local_fs import METADATA_DIR
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError
from app.settings_contract import ComputeMode

pytestmark = pytest.mark.asyncio

MARK = {"bioaf-backup-format": "1", "bioaf-sha256": "ab" * 32}


@pytest.fixture(params=["gcs-local", "s3-local", "nfs"])
def local_store(request, monkeypatch, tmp_path):
    """Every backend that keeps objects as files, with a URI builder for its scheme."""
    if request.param == "nfs":
        return NfsStorageProvider(root=str(tmp_path)), tmp_path
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    if request.param == "gcs-local":
        from app.adapters.storage import gcs

        monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path))
        return GcsStorageProvider(), tmp_path
    from app.adapters.storage import s3

    monkeypatch.setattr(s3, "LOCAL_DATA_ROOT", str(tmp_path))
    return S3StorageProvider(org_slug="testorg"), tmp_path


def _uri(adapter, key: str) -> str:
    return adapter.build_uri("bk", key)


# --- validation ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"Upper": "x"},
        {"has space": "x"},
        {"": "x"},
        {"k": "line\nbreak"},
        {"k": "é"},
        {"k": 5},
        {"k": "x" * 1025},
        {f"k{i}": "x" * 1000 for i in range(3)},  # over the 2 KiB total that S3 allows
    ],
)
async def test_invalid_metadata_is_refused(bad):
    with pytest.raises(ValidationError):
        validate_object_metadata(bad)


async def test_invalid_metadata_is_refused_before_anything_is_written(local_store, tmp_path):
    adapter, _ = local_store
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    with pytest.raises(ValidationError):
        await adapter.upload_filename(_uri(adapter, "a/b.dump"), str(src), metadata={"Bad Key": "x"})
    assert not await adapter.exists(_uri(adapter, "a/b.dump"))


# --- the local filesystem store ---------------------------------------------------------------------------


async def test_metadata_round_trips_and_omitting_it_keeps_todays_behavior(local_store, tmp_path):
    adapter, _ = local_store
    src = tmp_path / "src.bin"
    src.write_bytes(b"dump bytes")
    marked, plain = _uri(adapter, "postgres/a.dump"), _uri(adapter, "postgres/b.dump")
    await adapter.upload_filename(marked, str(src), metadata=MARK)
    await adapter.upload_file(plain, io.BytesIO(b"plain"))
    assert (await adapter.get_object_metadata(marked)).metadata == MARK
    assert (await adapter.get_object_metadata(plain)).metadata == {}
    assert (await adapter.get_object_metadata(marked)).size_bytes == len(b"dump bytes")


async def test_the_metadata_record_is_private_and_never_listed(local_store, tmp_path):
    adapter, root = local_store
    await adapter.upload_file(_uri(adapter, "postgres/a.dump"), io.BytesIO(b"x"), metadata=MARK)
    listed = [o.storage_uri for o in await adapter.list_objects(_uri(adapter, ""))]
    assert listed == [_uri(adapter, "postgres/a.dump")]
    records = [p for p in root.rglob("*") if METADATA_DIR in p.parts]
    assert records
    top = next(p for p in root.rglob(METADATA_DIR))
    assert stat.S_IMODE(top.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in records if p.is_file())


async def test_overwriting_without_metadata_drops_the_old_record(local_store):
    adapter, _ = local_store
    uri = _uri(adapter, "postgres/a.dump")
    await adapter.upload_file(uri, io.BytesIO(b"first"), metadata=MARK)
    await adapter.upload_file(uri, io.BytesIO(b"second"))
    assert (await adapter.get_object_metadata(uri)).metadata == {}
    await adapter.upload_file(uri, io.BytesIO(b"third"), metadata=MARK)
    await adapter.write_bytes(uri, b"fourth")
    assert (await adapter.get_object_metadata(uri)).metadata == {}


async def test_copy_and_move_carry_metadata_and_delete_removes_it(local_store):
    adapter, _ = local_store
    a, b, c = (_uri(adapter, f"postgres/{n}.dump") for n in "abc")
    await adapter.upload_file(a, io.BytesIO(b"x"), metadata=MARK)
    await adapter.copy(a, b)
    assert (await adapter.get_object_metadata(b)).metadata == MARK
    await adapter.move(b, c)
    assert (await adapter.get_object_metadata(c)).metadata == MARK
    with pytest.raises(StorageObjectNotFound):
        await adapter.get_object_metadata(b)
    await adapter.delete(c)
    await adapter.upload_file(c, io.BytesIO(b"new"))
    assert (await adapter.get_object_metadata(c)).metadata == {}


async def test_copying_an_unmarked_object_clears_the_destinations_record(local_store):
    adapter, _ = local_store
    a, b = _uri(adapter, "postgres/a.dump"), _uri(adapter, "postgres/b.dump")
    await adapter.upload_file(a, io.BytesIO(b"x"))
    await adapter.upload_file(b, io.BytesIO(b"y"), metadata=MARK)
    await adapter.copy(a, b)
    assert (await adapter.get_object_metadata(b)).metadata == {}


async def test_the_metadata_area_is_not_an_addressable_container(local_store):
    adapter, _ = local_store
    with pytest.raises(ValidationError):
        await adapter.write_bytes(adapter.build_uri(METADATA_DIR, "x"), b"x")


async def test_a_failed_upload_leaves_no_metadata(local_store, tmp_path):
    adapter, _ = local_store
    uri = _uri(adapter, "postgres/a.dump")
    with pytest.raises(OSError):
        await adapter.upload_filename(uri, str(tmp_path / "missing.bin"), metadata=MARK)
    assert not await adapter.exists(uri)
    await adapter.upload_file(uri, io.BytesIO(b"later"))
    assert (await adapter.get_object_metadata(uri)).metadata == {}


# --- cloud backends (mocked SDK clients) ------------------------------------------------------------------


@pytest.fixture
def gcs_cloud(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(GcsStorageProvider, mode=ComputeMode.KUBERNETES)
    blobs: dict[str, MagicMock] = {}

    def blob_for(key):
        if key not in blobs:
            blob = MagicMock()
            blob.metadata = None
            blob.size, blob.md5_hash, blob.content_type, blob.storage_class, blob.updated = 4, "m", None, "S", None
            uploaded_with: dict = {}

            def upload(path, content_type=None):
                uploaded_with["metadata"] = blob.metadata

            blob.upload_from_filename.side_effect = upload
            blob.uploaded_with = uploaded_with
            blobs[key] = blob
        return blobs[key]

    client = MagicMock()
    client.bucket.return_value.blob.side_effect = blob_for

    async def creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", creds)
    monkeypatch.setattr(adapter, "_get_gcs_client", lambda credentials=None: client)
    return adapter, blobs


async def test_gcs_sends_metadata_with_the_upload_and_reads_it_back(gcs_cloud, tmp_path):
    adapter, blobs = gcs_cloud
    src = tmp_path / "f"
    src.write_bytes(b"data")
    await adapter.upload_filename("gs://bk/postgres/a.dump", str(src), metadata=MARK)
    blob = blobs["postgres/a.dump"]
    assert blob.uploaded_with["metadata"] == MARK  # set before the bytes were sent, not patched on after
    blob.exists.return_value = True
    assert (await adapter.get_object_metadata("gs://bk/postgres/a.dump")).metadata == MARK


async def test_gcs_without_metadata_sends_none(gcs_cloud, tmp_path):
    adapter, blobs = gcs_cloud
    src = tmp_path / "f"
    src.write_bytes(b"data")
    await adapter.upload_filename("gs://bk/postgres/b.dump", str(src))
    assert blobs["postgres/b.dump"].uploaded_with["metadata"] is None


@pytest.fixture
def s3_cloud(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(S3StorageProvider, org_slug="testorg", mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    client = MagicMock()

    async def creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", creds)
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: client)
    return adapter, client


async def test_s3_sends_metadata_with_the_upload_including_multipart(s3_cloud, tmp_path):
    """boto3's upload_file/upload_fileobj carry ExtraArgs Metadata on single-part and multipart uploads alike."""
    adapter, client = s3_cloud
    src = tmp_path / "f"
    src.write_bytes(b"data")
    await adapter.upload_filename("s3://bk/postgres/a.dump", str(src), metadata=MARK)
    client.upload_file.assert_called_once_with(str(src), "bk", "postgres/a.dump", ExtraArgs={"Metadata": MARK})
    await adapter.upload_file("s3://bk/postgres/b.dump", io.BytesIO(b"x"), content_type="t", metadata=MARK)
    assert client.upload_fileobj.call_args.kwargs["ExtraArgs"] == {"ContentType": "t", "Metadata": MARK}


async def test_s3_reads_metadata_back(s3_cloud):
    adapter, client = s3_cloud
    client.head_object.return_value = {"ContentLength": 4, "ETag": '"abc"', "Metadata": dict(MARK)}
    assert (await adapter.get_object_metadata("s3://bk/postgres/a.dump")).metadata == MARK
    client.head_object.return_value = {"ContentLength": 4, "ETag": '"abc"'}
    assert (await adapter.get_object_metadata("s3://bk/postgres/a.dump")).metadata == {}


async def test_os_umask_does_not_widen_the_record(local_store, monkeypatch):
    adapter, root = local_store
    old = os.umask(0)
    try:
        await adapter.upload_file(_uri(adapter, "postgres/a.dump"), io.BytesIO(b"x"), metadata=MARK)
    finally:
        os.umask(old)
    files = [p for p in root.rglob("*") if METADATA_DIR in p.parts and p.is_file()]
    assert files and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)


# --- confinement of the metadata area (review finding 1) ---------------------------------------------------


def _metadata_root(root) -> "os.PathLike":
    import pathlib

    found = [p for p in pathlib.Path(root).rglob(METADATA_DIR)]
    return found[0] if found else None


@pytest.mark.parametrize("where", ["container", "key-dir", "area"])
async def test_a_symlink_in_the_metadata_area_never_reaches_outside_the_root(local_store, tmp_path, where):
    """Reads, writes and deletes of metadata refuse a link out of the store, and touch nothing outside."""
    import pathlib

    adapter, root = local_store
    uri = _uri(adapter, "postgres/a.dump")
    await adapter.upload_file(uri, io.BytesIO(b"x"), metadata=MARK)
    meta_root = _metadata_root(root)
    outside = tmp_path.parent / f"outside-{where}-{tmp_path.name}"
    (outside / "postgres").mkdir(parents=True)
    victim = outside / "postgres" / "a.dump"
    victim.write_text('{"bioaf-sha256": "planted"}')
    if where == "container":
        target = meta_root / "bk"
        import shutil

        shutil.rmtree(target)
        target.symlink_to(outside)
    elif where == "key-dir":
        target = meta_root / "bk" / "postgres"
        import shutil

        shutil.rmtree(target)
        target.symlink_to(outside / "postgres")
    else:
        import shutil

        shutil.rmtree(meta_root)
        bk = outside / "bk"
        bk.mkdir()
        (bk / "postgres").mkdir()
        (bk / "postgres" / "a.dump").write_text('{"bioaf-sha256": "planted"}')
        victim = bk / "postgres" / "a.dump"
        pathlib.Path(meta_root).symlink_to(outside)

    with pytest.raises(ValidationError):
        await adapter.get_object_metadata(uri)
    with pytest.raises(ValidationError):
        await adapter.delete(uri)
    with pytest.raises(ValidationError):
        await adapter.upload_file(uri, io.BytesIO(b"y"), metadata=MARK)
    assert victim.read_text() == '{"bioaf-sha256": "planted"}'
