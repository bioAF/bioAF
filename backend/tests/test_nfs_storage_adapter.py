"""Phase 7: the real NFS storage adapter.

NfsStorageProvider implements the full Phase 3 StorageProvider object-store
interface against a shared filesystem rooted at a configured mount, with logical
stores mapped to subdirectories. It declares signed_url_upload=False (a filesystem
cannot mint signed URLs), confines every path under the mount root (no traversal),
and reports coarse usage metrics. These tests use a tmpdir as the mount.
"""

import io

import pytest

from app.exceptions import ValidationError

from app.adapters.capabilities import CapabilityNotSupported
from app.adapters.models import StorageObjectNotFound, StorageStore
from app.adapters.storage.nfs import NfsStorageProvider


@pytest.fixture
def nfs(tmp_path):
    return NfsStorageProvider(root=str(tmp_path))


# -- capabilities -------------------------------------------------------------


def test_nfs_declares_no_signed_url_upload(nfs):
    caps = nfs.capabilities()
    assert caps.signed_url_upload is False
    assert caps.storage_tier_metrics is False


# -- build_uri / parse_uri ----------------------------------------------------
#
# The NFS analogue of the GCS minting: a backend URI is file://<store>/<key>, so
# build_uri / parse_uri round-trip on that scheme instead of gs://.


def test_build_uri_mints_file_scheme(nfs):
    assert nfs.build_uri("working", "a/b.txt") == "file://working/a/b.txt"


def test_build_uri_strips_leading_slash_on_key(nfs):
    assert nfs.build_uri("working", "/a/b.txt") == "file://working/a/b.txt"


def test_parse_uri_round_trips_build_uri(nfs):
    uri = nfs.build_uri("working", "a/b.txt")
    assert nfs.parse_uri(uri) == ("working", "a/b.txt")


def test_parse_uri_rejects_non_file_scheme(nfs):
    with pytest.raises(ValidationError):
        nfs.parse_uri("gs://bucket/a/b.txt")


def test_cli_auth_command_is_empty_for_nfs(nfs):
    # A mounted filesystem needs no CLI authentication step.
    assert nfs.cli_auth_command("/secrets/gcp/key.json") == ""


def test_cli_copy_commands_use_plain_cp(nfs):
    copy_in = nfs.cli_copy_in("file://working/a/b.txt", "/data/b.txt")
    assert copy_in.startswith("cp ") and copy_in.endswith(" /data/b.txt")
    copy_out = nfs.cli_copy_out("/outputs/*", "file://working/runs/1/")
    assert copy_out.startswith("cp -r /outputs/* ")


def test_image_storage_pip_packages_empty_for_nfs(nfs):
    # A mounted filesystem needs no cloud storage client library.
    assert nfs.image_storage_pip_packages() == ""


# -- object-store CRUD --------------------------------------------------------


@pytest.mark.asyncio
async def test_write_read_text_round_trip(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "a/b/hello.txt")
    await nfs.write_text(uri, "hello nfs")
    assert await nfs.read_text(uri) == "hello nfs"
    assert await nfs.exists(uri) is True


@pytest.mark.asyncio
async def test_write_read_bytes_round_trip(nfs):
    uri = await nfs.resolve_uri(StorageStore.RAW, "data.bin")
    await nfs.write_bytes(uri, b"\x00\x01\x02")
    assert await nfs.read_bytes(uri) == b"\x00\x01\x02"


@pytest.mark.asyncio
async def test_read_missing_raises_storage_object_not_found(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "nope.txt")
    with pytest.raises(StorageObjectNotFound):
        await nfs.read_bytes(uri)


@pytest.mark.asyncio
async def test_delete_is_idempotent(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "x.txt")
    await nfs.write_text(uri, "x")
    await nfs.delete(uri)
    assert await nfs.exists(uri) is False
    # second delete on a missing object is not an error
    await nfs.delete(uri)


@pytest.mark.asyncio
async def test_upload_file_and_download_to_file(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "stream.bin")
    await nfs.upload_file(uri, io.BytesIO(b"streamed-bytes"))
    sink = io.BytesIO()
    await nfs.download_to_file(uri, sink)
    assert sink.getvalue() == b"streamed-bytes"


@pytest.mark.asyncio
async def test_upload_and_download_filename(nfs, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("from-local")
    uri = await nfs.resolve_uri(StorageStore.RESULTS, "out/src.txt")
    await nfs.upload_filename(uri, str(src))
    dest = tmp_path / "back.txt"
    await nfs.download_to_filename(uri, str(dest))
    assert dest.read_text() == "from-local"


@pytest.mark.asyncio
async def test_get_object_metadata(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "m.txt")
    await nfs.write_text(uri, "12345")
    meta = await nfs.get_object_metadata(uri)
    assert meta.uri == uri
    assert meta.size_bytes == 5


@pytest.mark.asyncio
async def test_get_object_metadata_missing_raises(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "absent.txt")
    with pytest.raises(StorageObjectNotFound):
        await nfs.get_object_metadata(uri)


@pytest.mark.asyncio
async def test_list_objects_under_prefix(nfs):
    for key in ["p/one.txt", "p/sub/two.txt", "other/three.txt"]:
        await nfs.write_text(await nfs.resolve_uri(StorageStore.WORKING, key), "x")
    prefix = await nfs.resolve_uri(StorageStore.WORKING, "p/")
    found = await nfs.list_objects(prefix)
    names = sorted(o.filename for o in found)
    assert names == ["one.txt", "two.txt"]
    # storage_uri round-trips back through the adapter
    for obj in found:
        assert await nfs.exists(obj.storage_uri)


@pytest.mark.asyncio
async def test_copy_and_move(nfs):
    src = await nfs.resolve_uri(StorageStore.WORKING, "src.txt")
    dst = await nfs.resolve_uri(StorageStore.RESULTS, "dst.txt")
    await nfs.write_text(src, "payload")

    await nfs.copy(src, dst)
    assert await nfs.read_text(dst) == "payload"
    assert await nfs.exists(src) is True  # copy leaves source

    moved = await nfs.resolve_uri(StorageStore.RESULTS, "moved.txt")
    await nfs.move(src, moved)
    assert await nfs.read_text(moved) == "payload"
    assert await nfs.exists(src) is False  # move removes source


@pytest.mark.asyncio
async def test_copy_missing_source_raises(nfs):
    src = await nfs.resolve_uri(StorageStore.WORKING, "ghost.txt")
    dst = await nfs.resolve_uri(StorageStore.RESULTS, "dst.txt")
    with pytest.raises(StorageObjectNotFound):
        await nfs.copy(src, dst)


# -- signed URLs are unsupported on a filesystem ------------------------------


@pytest.mark.asyncio
async def test_nfs_signed_url_raises_capability_not_supported(nfs):
    uri = await nfs.resolve_uri(StorageStore.WORKING, "x.txt")
    with pytest.raises(CapabilityNotSupported):
        await nfs.generate_signed_url(uri)


@pytest.mark.asyncio
async def test_nfs_resumable_upload_raises_capability_not_supported(nfs):
    uri = await nfs.resolve_uri(StorageStore.INGEST, "x.fastq")
    with pytest.raises(CapabilityNotSupported):
        await nfs.create_resumable_upload_url(uri)


# -- security: confine all paths under the mount root -------------------------


@pytest.mark.asyncio
async def test_nfs_path_traversal_blocked(nfs):
    # A crafted key trying to escape the store/root must be rejected, not write
    # outside the mount.
    evil = "file://working/../../../../etc/passwd"
    with pytest.raises(ValidationError):
        await nfs.write_text(evil, "pwned")


# -- metrics ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_metrics_reports_usage(nfs):
    await nfs.write_bytes(await nfs.resolve_uri(StorageStore.RAW, "big.bin"), b"x" * 1024)
    metrics = await nfs.get_storage_metrics()
    assert metrics.total_size_gb >= 0.0
    # Filesystem capacity is surfaced for admins even though there are no tiers.
    assert "filesystem" in metrics.provider_details


@pytest.mark.asyncio
async def test_bucket_admin_metrics_degenerate(nfs):
    """NFS has no buckets: the bucket-admin enumeration returns the neutral
    default rather than reaching for a cloud SDK."""
    result = await nfs.get_bucket_admin_metrics("anything")
    assert result.size_bytes == 0
    assert result.object_count == 0
    assert result.lifecycle_summaries == []
    assert result.versioning_enabled is False


# -- integration: stage inputs -> collect outputs -----------------------------


@pytest.mark.asyncio
async def test_nfs_integration_stage_and_collect(nfs, tmp_path):
    working = str(tmp_path / "work")
    staged = await nfs.stage_inputs([{"filename": "reads.fastq", "local_path": None}], working)
    assert len(staged) == 1
    # Simulate a pipeline writing an output into the working dir.
    import os

    with open(os.path.join(working, "result.txt"), "w") as f:
        f.write("done")

    outputs = await nfs.collect_outputs(working, {"id": 7, "experiment_id": 3})
    names = {o.filename for o in outputs}
    assert "result.txt" in names
    # Collected outputs are addressable storage URIs that read back.
    for obj in outputs:
        if obj.filename == "result.txt":
            assert await nfs.read_text(obj.storage_uri) == "done"


# -- parity: the same caller code works against GCS (local) and NFS -----------


@pytest.mark.asyncio
async def test_storage_interface_parity_gcs_vs_nfs(tmp_path, monkeypatch):
    """Identical caller code (resolve_uri -> write -> read -> list -> delete)
    produces the same observable behavior on both the GCS adapter (local mode)
    and the NFS adapter: the abstraction holds across backends."""
    from app.adapters.storage import gcs as gcs_mod

    monkeypatch.setattr(gcs_mod, "LOCAL_DATA_ROOT", str(tmp_path / "gcs"))
    gcs = gcs_mod.GcsStorageProvider()
    assert gcs.is_local  # parity test exercises the GCS filesystem-emulation path
    nfs = NfsStorageProvider(root=str(tmp_path / "nfs"))

    for adapter in (gcs, nfs):
        uri = await adapter.resolve_uri(StorageStore.WORKING, "dir/file.txt")
        await adapter.write_text(uri, "same code")
        assert await adapter.read_text(uri) == "same code"
        assert await adapter.exists(uri) is True

        listed = await adapter.list_objects(await adapter.resolve_uri(StorageStore.WORKING, "dir/"))
        assert [o.filename for o in listed] == ["file.txt"]

        await adapter.delete(uri)
        assert await adapter.exists(uri) is False
