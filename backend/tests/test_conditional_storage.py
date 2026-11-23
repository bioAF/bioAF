"""Create-only, source-pinned copies and identity-guarded deletion (project 12B.1b).

Filing and organization copy an object to a destination only if nothing is there yet, only from
the exact source version they observed, and stamp it with server-owned ownership metadata in the
same write. Cleanup deletes only the exact version it observed. Existing replacement copies and
version wipes keep working unchanged for their callers.
"""

from __future__ import annotations

import io
import os
import threading

import pytest

from app.adapters.models import DestinationExists, ObjectChanged, ObjectIdentity, StorageObjectNotFound
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.local_fs import LocalObjectFilesystem
from app.adapters.storage.nfs import NfsStorageProvider
from app.exceptions import ValidationError
from app.settings_contract import ComputeMode
from tests.support.fake_gcs import FakeClient, FakeGcs

pytestmark = pytest.mark.asyncio

OWNER = {"bioaf-file-uuid": "0f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a", "bioaf-organization-id": "1"}


@pytest.fixture(params=["gcs-local", "nfs"])
def store(request, monkeypatch, tmp_path):
    if request.param == "nfs":
        return NfsStorageProvider(root=str(tmp_path))
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    from app.adapters.storage import gcs

    monkeypatch.setattr(gcs, "LOCAL_DATA_ROOT", str(tmp_path))
    return GcsStorageProvider()


def _u(adapter, key):
    return adapter.build_uri("bk", key)


async def _put(adapter, key, data, metadata=None):
    uri = _u(adapter, key)
    await adapter.upload_file(uri, io.BytesIO(data), metadata=metadata)
    return uri


# ---------------------------------------------------------------------------
# filesystem store (GCS/S3 local mode, NFS)
# ---------------------------------------------------------------------------


async def test_a_create_only_copy_publishes_the_pinned_source_with_owner_metadata(store):
    src = await _put(store, "uploads/1/a.txt", b"first-science", {"bioaf-file-uuid": "forged", "lab-tag": "x"})
    pinned = await store.object_identity(src)
    dest = _u(store, "experiments/1/files/u/a.txt")

    published = await store.copy_create_only(src, dest, source_identity=pinned, metadata=OWNER)

    assert await store.read_bytes(dest) == b"first-science"
    assert published.uri == dest and published.size_bytes == 13
    seen = await store.inspect_object(dest)
    assert seen.identity == published
    # Reserved keys come from the server, never the source; unrelated metadata is kept.
    assert seen.metadata == {**OWNER, "lab-tag": "x"}
    assert await store.read_bytes(src) == b"first-science"  # a copy never removes its source


async def test_a_create_only_copy_never_replaces_an_existing_destination(store):
    src = await _put(store, "uploads/2/same.txt", b"second-science")
    dest = await _put(store, "experiments/1/files/u/same.txt", b"first-science", {"bioaf-file-uuid": "other"})

    with pytest.raises(DestinationExists):
        await store.copy_create_only(src, dest, source_identity=await store.object_identity(src), metadata=OWNER)

    assert await store.read_bytes(dest) == b"first-science"
    assert (await store.get_object_metadata(dest)).metadata == {"bioaf-file-uuid": "other"}
    assert await store.read_bytes(src) == b"second-science"


async def test_a_changed_source_is_refused_and_publishes_nothing(store):
    src = await _put(store, "uploads/3/a.txt", b"observed")
    pinned = await store.object_identity(src)
    await store.upload_file(src, io.BytesIO(b"replaced!"))  # a different version now
    dest = _u(store, "experiments/1/files/u/a.txt")

    with pytest.raises(ObjectChanged):
        await store.copy_create_only(src, dest, source_identity=pinned, metadata=OWNER)
    assert not await store.exists(dest)


async def test_a_failed_copy_leaves_no_destination_and_no_partial_file(store, monkeypatch, tmp_path):
    src = await _put(store, "uploads/4/a.txt", b"x" * 4096)
    dest = _u(store, "experiments/1/files/u/a.txt")

    import app.adapters.storage.local_fs as local_fs

    def broken(*args, **kwargs):
        raise OSError("disk full")

    pinned = await store.object_identity(src)
    with monkeypatch.context() as m:
        m.setattr(local_fs.shutil, "copyfileobj", broken)
        with pytest.raises(OSError):
            await store.copy_create_only(src, dest, source_identity=pinned, metadata=OWNER)
    assert not await store.exists(dest)
    leftovers = [n for _d, _s, names in os.walk(tmp_path) for n in names if "partial" in n]
    assert leftovers == []
    assert await store.read_bytes(src) == b"x" * 4096


async def test_a_create_only_copy_stays_inside_the_store(store):
    src = await _put(store, "uploads/5/a.txt", b"x")
    with pytest.raises(ValidationError):
        await store.copy(src, _u(store, "../escape.txt"), create_only=True, metadata=OWNER)


async def test_a_guarded_delete_removes_only_the_observed_version(store):
    uri = await _put(store, "uploads/6/a.txt", b"v1", {"lab-tag": "x"})
    observed = await store.object_identity(uri)
    assert await store.delete_if_unchanged(observed) is True
    assert not await store.exists(uri)
    with pytest.raises(StorageObjectNotFound):
        await store.get_object_metadata(uri)
    # Already gone: nothing to do, not an error.
    assert await store.delete_if_unchanged(observed) is False


async def test_a_guarded_delete_refuses_a_replaced_object(store):
    uri = await _put(store, "uploads/7/a.txt", b"v1")
    observed = await store.object_identity(uri)
    await store.upload_file(uri, io.BytesIO(b"version-two"))
    with pytest.raises(ObjectChanged):
        await store.delete_if_unchanged(observed)
    assert await store.read_bytes(uri) == b"version-two"


async def test_a_guarded_delete_refuses_a_foreign_identity_kind(store):
    uri = await _put(store, "uploads/8/a.txt", b"v1")
    with pytest.raises(ObjectChanged):
        await store.delete_if_unchanged(ObjectIdentity(uri=uri, size_bytes=2, version="generation:12"))
    assert await store.exists(uri)


async def test_the_create_only_option_on_copy_uses_the_same_contract(store):
    src = await _put(store, "uploads/9/a.txt", b"x")
    dest = await _put(store, "experiments/1/files/u/a.txt", b"taken")
    with pytest.raises(DestinationExists):
        await store.copy(src, dest, create_only=True)
    fresh = _u(store, "experiments/1/files/v/a.txt")
    assert await store.copy(src, fresh, create_only=True) == fresh
    assert await store.read_bytes(fresh) == b"x"


async def test_replacement_copies_and_plain_deletes_are_unchanged(store):
    src = await _put(store, "a/src.txt", b"new")
    dest = await _put(store, "a/dest.txt", b"old")
    assert await store.copy(src, dest) == dest
    assert await store.read_bytes(dest) == b"new"
    await store.delete(dest)
    assert not await store.exists(dest)


def test_concurrent_create_only_copies_publish_exactly_one(tmp_path):
    """Real threads racing on one destination: one wins, the rest are refused, nothing is mixed."""
    fs = LocalObjectFilesystem(str(tmp_path), lambda u: tuple(u.split("://", 1)[1].split("/", 1)), _build)
    sources = []
    for n in range(8):
        uri = f"gs://bk/uploads/{n}/same.txt"
        fs.write_stream(uri, io.BytesIO(f"payload-{n}-".encode() * 50_000))
        sources.append(uri)
    dest = "gs://bk/experiments/1/files/u/same.txt"
    barrier = threading.Barrier(len(sources))
    outcomes: list = [None] * len(sources)

    def run(i):
        barrier.wait()
        try:
            fs.copy_create_only(sources[i], dest, None, OWNER)
            outcomes[i] = "won"
        except DestinationExists:
            outcomes[i] = "refused"

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(sources))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("won") == 1 and outcomes.count("refused") == len(sources) - 1
    winner = outcomes.index("won")
    assert fs.read_bytes(dest) == fs.read_bytes(sources[winner])
    assert all(fs.exists(s) for s in sources)


def _build(bucket, key):
    return f"gs://{bucket}/{key}"


# ---------------------------------------------------------------------------
# Cloud Storage
# ---------------------------------------------------------------------------


@pytest.fixture
def gcs(mode_adapter_factory, monkeypatch):
    adapter = mode_adapter_factory(GcsStorageProvider, mode=ComputeMode.KUBERNETES)
    fake = FakeGcs()

    async def creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", creds)
    monkeypatch.setattr(adapter, "_get_gcs_client", lambda credentials=None: FakeClient(fake))
    return adapter, fake


async def test_gcs_publishes_with_preconditions_and_metadata_in_one_rewrite(gcs):
    adapter, fake = gcs
    src = fake.put("ingest", "uploads/1/a.txt", b"first-science", metadata={"lab-tag": "x"}, content_type="text/plain")
    pinned = await adapter.object_identity("gs://ingest/uploads/1/a.txt")
    assert pinned.version == f"generation:{src.generation}"

    published = await adapter.copy_create_only(
        "gs://ingest/uploads/1/a.txt",
        "gs://raw/experiments/1/files/u/a.txt",
        source_identity=pinned,
        metadata=OWNER,
    )

    stored = fake.get("raw", "experiments/1/files/u/a.txt")
    assert stored.data == b"first-science"
    assert stored.metadata == {**OWNER, "lab-tag": "x"} and stored.content_type == "text/plain"
    assert published.version == f"generation:{stored.generation}" and published.size_bytes == 13
    calls = fake.rewrite_calls
    assert len(calls) == 2 and calls[1]["token"] == "token-1"  # continued, not restarted
    assert all(c["if_generation_match"] == 0 and c["if_source_generation_match"] == src.generation for c in calls)
    assert fake.get("ingest", "uploads/1/a.txt") is not None


async def test_gcs_refuses_an_existing_destination_without_changing_it(gcs):
    adapter, fake = gcs
    fake.put("ingest", "uploads/2/same.txt", b"second-science")
    fake.put("raw", "experiments/1/files/u/same.txt", b"first-science", metadata={"bioaf-file-uuid": "other"})
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only(
            "gs://ingest/uploads/2/same.txt", "gs://raw/experiments/1/files/u/same.txt", metadata=OWNER
        )
    assert fake.get("raw", "experiments/1/files/u/same.txt").data == b"first-science"


async def test_gcs_refuses_a_destination_created_while_copying(gcs):
    adapter, fake = gcs
    fake.put("ingest", "uploads/3/same.txt", b"mine")
    fake.before_publish = lambda: fake.put("raw", "experiments/1/files/u/same.txt", b"theirs")
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only(
            "gs://ingest/uploads/3/same.txt", "gs://raw/experiments/1/files/u/same.txt", metadata=OWNER
        )
    assert fake.get("raw", "experiments/1/files/u/same.txt").data == b"theirs"
    assert fake.get("ingest", "uploads/3/same.txt").data == b"mine"


async def test_gcs_refuses_a_source_replaced_after_it_was_observed(gcs):
    adapter, fake = gcs
    fake.put("ingest", "uploads/4/a.txt", b"observed")
    pinned = await adapter.object_identity("gs://ingest/uploads/4/a.txt")
    fake.put("ingest", "uploads/4/a.txt", b"replaced")
    with pytest.raises(ObjectChanged):
        await adapter.copy_create_only(
            "gs://ingest/uploads/4/a.txt",
            "gs://raw/experiments/1/files/u/a.txt",
            source_identity=pinned,
            metadata=OWNER,
        )
    assert fake.get("raw", "experiments/1/files/u/a.txt") is None


async def test_gcs_guarded_delete_is_generation_qualified(gcs):
    adapter, fake = gcs
    fake.put("ingest", "uploads/5/a.txt", b"v1")
    observed = await adapter.object_identity("gs://ingest/uploads/5/a.txt")
    fake.put("ingest", "uploads/5/a.txt", b"v2")
    with pytest.raises(ObjectChanged):
        await adapter.delete_if_unchanged(observed)
    assert fake.get("ingest", "uploads/5/a.txt").data == b"v2"
    current = await adapter.object_identity("gs://ingest/uploads/5/a.txt")
    assert await adapter.delete_if_unchanged(current) is True
    assert fake.get("ingest", "uploads/5/a.txt") is None
    assert await adapter.delete_if_unchanged(current) is False
    assert fake.delete_calls[-1]["if_generation_match"] == int(current.version.split(":")[1])


async def test_gcs_inspection_reads_identity_and_metadata_together(gcs):
    adapter, fake = gcs
    stored = fake.put("raw", "experiments/1/files/u/a.txt", b"abc", metadata=OWNER)
    seen = await adapter.inspect_object("gs://raw/experiments/1/files/u/a.txt")
    assert seen.identity.version == f"generation:{stored.generation}" and seen.identity.size_bytes == 3
    assert seen.metadata == OWNER
    with pytest.raises(StorageObjectNotFound):
        await adapter.inspect_object("gs://raw/missing")


async def test_gcs_conditional_writes_are_tracked_storage_workers(gcs, monkeypatch):
    from app.adapters.storage import workers

    adapter, fake = gcs
    fake.put("ingest", "uploads/6/a.txt", b"x")
    labels = []
    real = workers.run

    async def run(label, fn, *args):
        labels.append(label)
        return await real(label, fn, *args)

    monkeypatch.setattr(workers, "run", run)
    copied = await adapter.copy_create_only("gs://ingest/uploads/6/a.txt", "gs://raw/x/a.txt", metadata=OWNER)
    await adapter.delete_if_unchanged(copied)
    assert labels == ["storage copy", "storage delete"]


# ---------------------------------------------------------------------------
# S3 (12B.2a)
# ---------------------------------------------------------------------------


@pytest.fixture(params=[True, False], ids=["versioned", "unversioned"])
def s3(request, mode_adapter_factory, monkeypatch):
    from app.adapters.storage.s3 import S3StorageProvider
    from tests.support.fake_s3 import FakeS3

    adapter = mode_adapter_factory(S3StorageProvider, org_slug="t", mode=ComputeMode.KUBERNETES)
    adapter._region = "us-east-1"
    fake = FakeS3(versioning=request.param)

    async def creds():
        return None

    monkeypatch.setattr(adapter, "_get_credentials", creds)
    monkeypatch.setattr(adapter, "_get_s3_client", lambda credentials=None: fake)
    return adapter, fake


async def test_s3_publishes_create_only_with_owner_metadata(s3):
    adapter, fake = s3
    fake.put("ingest", "uploads/1/a.txt", b"first-science", {"lab-tag": "x", "bioaf-file-uuid": "forged"}, "text/plain")
    pinned = await adapter.object_identity("s3://ingest/uploads/1/a.txt")
    published = await adapter.copy_create_only(
        "s3://ingest/uploads/1/a.txt", "s3://raw/e/files/u/a.txt", source_identity=pinned, metadata=OWNER
    )
    stored = fake.objects[("raw", "e/files/u/a.txt")]
    assert stored.data == b"first-science" and stored.content_type == "text/plain"
    assert stored.metadata == {**OWNER, "lab-tag": "x"}
    assert published.size_bytes == 13
    [call] = [c for c in fake.calls if c[0] == "copy_object"]
    assert call[1]["IfNoneMatch"] == "*" and call[1]["MetadataDirective"] == "REPLACE"
    assert ("ingest", "uploads/1/a.txt") in fake.objects


async def test_s3_refuses_an_existing_or_racing_destination(s3):
    adapter, fake = s3
    fake.put("ingest", "uploads/2/a.txt", b"mine")
    fake.put("raw", "taken.txt", b"theirs")
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only("s3://ingest/uploads/2/a.txt", "s3://raw/taken.txt", metadata=OWNER)
    fake.before_publish = lambda: fake.put("raw", "racing.txt", b"theirs")
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only("s3://ingest/uploads/2/a.txt", "s3://raw/racing.txt", metadata=OWNER)
    assert fake.objects[("raw", "taken.txt")].data == fake.objects[("raw", "racing.txt")].data == b"theirs"


async def test_s3_refuses_a_replaced_source(s3):
    adapter, fake = s3
    fake.put("ingest", "uploads/3/a.txt", b"observed")
    pinned = await adapter.object_identity("s3://ingest/uploads/3/a.txt")
    fake.put("ingest", "uploads/3/a.txt", b"replaced")
    if fake.versioning:
        # The pinned version still exists: it, not the replacement, is what gets copied.
        published = await adapter.copy_create_only(
            "s3://ingest/uploads/3/a.txt", "s3://raw/a.txt", source_identity=pinned, metadata=OWNER
        )
        assert fake.objects[("raw", "a.txt")].data == b"observed" and published.size_bytes == 8
    else:
        with pytest.raises(ObjectChanged):
            await adapter.copy_create_only(
                "s3://ingest/uploads/3/a.txt", "s3://raw/a.txt", source_identity=pinned, metadata=OWNER
            )
        assert ("raw", "a.txt") not in fake.objects


async def test_s3_large_objects_are_copied_in_parts_with_a_conditional_completion(s3, monkeypatch):
    from app.adapters.storage import s3 as module

    adapter, fake = s3
    monkeypatch.setattr(module, "_SINGLE_COPY_MAX", 10)
    monkeypatch.setattr(module, "_COPY_PART", 4)
    fake.put("ingest", "big", b"0123456789abcdef!", {"lab-tag": "x"})
    published = await adapter.copy_create_only("s3://ingest/big", "s3://raw/big", metadata=OWNER)
    assert fake.objects[("raw", "big")].data == b"0123456789abcdef!"
    assert fake.objects[("raw", "big")].metadata == {**OWNER, "lab-tag": "x"}
    assert published.native_md5 is None  # a multipart ETag is not an MD5
    assert [c[1]["IfNoneMatch"] for c in fake.calls if c[0] == "complete_multipart_upload"] == ["*"]

    fake.before_publish = lambda: fake.put("raw", "big2", b"theirs")
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only("s3://ingest/big", "s3://raw/big2", metadata=OWNER)
    assert fake.objects[("raw", "big2")].data == b"theirs"
    assert len(fake.aborted) == 1 and fake.uploads == {}  # only its own unfinished upload was aborted


async def test_s3_guarded_delete_removes_only_the_observed_version(s3):
    adapter, fake = s3
    fake.put("ingest", "uploads/4/a.txt", b"v1")
    observed = await adapter.object_identity("s3://ingest/uploads/4/a.txt")
    fake.put("ingest", "uploads/4/a.txt", b"v2")
    with pytest.raises(ObjectChanged):
        await adapter.delete_if_unchanged(observed)
    assert fake.objects[("ingest", "uploads/4/a.txt")].data == b"v2"
    current = await adapter.object_identity("s3://ingest/uploads/4/a.txt")
    assert await adapter.delete_if_unchanged(current) is True
    if fake.versioning:
        # Only the observed version went; the older one is current again and is not touched.
        assert fake.objects[("ingest", "uploads/4/a.txt")].data == b"v1"
        with pytest.raises(ObjectChanged):
            await adapter.delete_if_unchanged(current)
    else:
        assert ("ingest", "uploads/4/a.txt") not in fake.objects
        assert await adapter.delete_if_unchanged(current) is False
    [last] = [c for c in fake.calls if c[0] == "delete_object"][-1:]
    assert (last[1]["VersionId"] is not None) == fake.versioning
    assert (last[1]["IfMatch"] is not None) == (not fake.versioning)


async def test_s3_inspection_reads_identity_and_metadata_together(s3):
    adapter, fake = s3
    fake.put("raw", "x", b"abc", OWNER)
    seen = await adapter.inspect_object("s3://raw/x")
    assert seen.metadata == OWNER and seen.identity.size_bytes == 3


async def test_s3_local_mode_uses_the_filesystem_primitives(monkeypatch, tmp_path):
    from app.adapters.storage import s3 as module
    from app.adapters.storage.s3 import S3StorageProvider

    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    monkeypatch.setattr(module, "LOCAL_DATA_ROOT", str(tmp_path))
    adapter = S3StorageProvider(org_slug="t")
    src = adapter.build_uri("bk", "uploads/1/a.txt")
    await adapter.upload_file(src, io.BytesIO(b"x"))
    dest = adapter.build_uri("bk", "e/files/u/a.txt")
    published = await adapter.copy_create_only(src, dest, source_identity=await adapter.object_identity(src))
    with pytest.raises(DestinationExists):
        await adapter.copy_create_only(src, dest)
    assert (await adapter.inspect_object(dest)).identity == published
    assert await adapter.delete_if_unchanged(published) is True


# ---------------------------------------------------------------------------
# confinement of the private lock and staging areas (review finding 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("private", [".bioaf-object-staging", ".bioaf-object-locks"])
def test_a_private_area_replaced_by_a_link_is_refused_and_nothing_leaves_the_store(tmp_path, private):
    root, outside = tmp_path / "store", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / private).symlink_to(outside)
    fs = LocalObjectFilesystem(str(root), lambda u: tuple(u.split("://", 1)[1].split("/", 1)), _build)
    fs.write_stream("gs://bk/uploads/1/a.txt", io.BytesIO(b"scientific bytes"))

    with pytest.raises(ValidationError):
        fs.copy_create_only("gs://bk/uploads/1/a.txt", "gs://bk/experiments/1/files/u/a.txt", None, OWNER)
    if private == ".bioaf-object-locks":  # a guarded delete takes the lock, not the staging area
        with pytest.raises(ValidationError):
            fs.delete_if_unchanged("gs://bk/uploads/1/a.txt", fs.identity("gs://bk/uploads/1/a.txt")[1])

    assert list(outside.iterdir()) == []
    assert not fs.exists("gs://bk/experiments/1/files/u/a.txt")
    assert fs.read_bytes("gs://bk/uploads/1/a.txt") == b"scientific bytes"


def test_a_private_area_that_is_a_file_is_refused(tmp_path):
    (tmp_path / ".bioaf-object-staging").write_text("not a directory")
    fs = LocalObjectFilesystem(str(tmp_path), lambda u: tuple(u.split("://", 1)[1].split("/", 1)), _build)
    fs.write_stream("gs://bk/a.txt", io.BytesIO(b"x"))
    with pytest.raises(ValidationError):
        fs.copy_create_only("gs://bk/a.txt", "gs://bk/b.txt", None, None)
    assert not fs.exists("gs://bk/b.txt")


async def test_s3_deleting_the_current_version_can_expose_an_older_one(s3):
    """The provider behavior review finding 5 depends on, modelled by the fake."""
    adapter, fake = s3
    if not fake.versioning:
        pytest.skip("only versioned buckets keep older versions")
    fake.put("ingest", "uploads/9/a.txt", b"first attempt")
    fake.put("ingest", "uploads/9/a.txt", b"second attempt")
    current = await adapter.object_identity("s3://ingest/uploads/9/a.txt")
    assert await adapter.delete_if_unchanged(current) is True
    assert fake.objects[("ingest", "uploads/9/a.txt")].data == b"first attempt"
