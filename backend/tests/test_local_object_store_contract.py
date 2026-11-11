"""One filesystem object-store contract, run against GCS local mode, S3 local mode and NFS.

The three adapters each carried their own copy of the same filesystem code, and the copies
disagreed with the containment their own resolvers enforced: listing built its root by hand, so
``gs://../`` and ``file://../`` listed files outside the object root, and a symlink to a file
outside the root was listed with its target's size although reading it was refused. Listing also
ignored ``recursive=False`` and returned one object for ``max_results=0``; a negative prefix length
read the whole file; deleting an internal symlink deleted its target.

Every assertion here uses real bytes on a temporary root (decisions: plan 17 findings, delivery 4
matrix).
"""

from __future__ import annotations

import io
import os

import pytest
import pytest_asyncio

from app.adapters.models import StorageObjectNotFound
from app.adapters.storage import gcs as gcs_module
from app.adapters.storage import s3 as s3_module
from app.adapters.storage.gcs import GcsStorageProvider
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.s3 import S3StorageProvider
from app.exceptions import ValidationError


class Backend:
    def __init__(self, name, storage, objects_root, bucket):
        self.name = name
        self.s = storage
        self.root = objects_root  # the directory buckets/stores live in
        self.bucket = bucket

    def uri(self, key: str, bucket: str | None = None) -> str:
        return self.s.build_uri(bucket or self.bucket, key)

    def path(self, key: str) -> str:
        return os.path.join(self.root, self.bucket, key)


@pytest.fixture(params=["gcs", "s3", "nfs"])
def b(request, tmp_path, monkeypatch):
    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "local")
    if request.param == "gcs":
        monkeypatch.setattr(gcs_module, "LOCAL_DATA_ROOT", str(tmp_path / "data"))
        return Backend("gcs", GcsStorageProvider(org_slug="t"), str(tmp_path / "data" / "_objects"), "bkt")
    if request.param == "s3":
        monkeypatch.setattr(s3_module, "LOCAL_DATA_ROOT", str(tmp_path / "data"))
        return Backend("s3", S3StorageProvider(org_slug="t"), str(tmp_path / "data" / "_objects"), "bkt")
    return Backend("nfs", NfsStorageProvider(root=str(tmp_path / "data")), str(tmp_path / "data"), "working")


@pytest.fixture
def outside(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"outside the root")
    return secret


# --- containment ----------------------------------------------------------------------------


class TestContainment:
    @pytest.mark.asyncio
    async def test_parent_bucket_names_cannot_list_outside_the_root(self, b, outside):
        await b.s.write_bytes(b.uri("k.txt"), b"in")
        for bad in ("..", "."):
            with pytest.raises(ValidationError):
                await b.s.list_objects(b.uri("", bucket=bad))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op", ["read_bytes", "exists", "delete", "get_object_metadata"])
    async def test_parent_bucket_names_are_refused_by_every_operation(self, b, outside, op):
        with pytest.raises(ValidationError):
            await getattr(b.s, op)(b.uri("secret.txt", bucket=".."))

    @pytest.mark.asyncio
    async def test_traversal_in_a_listing_prefix_is_refused(self, b):
        with pytest.raises(ValidationError):
            await b.s.list_objects(b.uri("a/../../"))

    @pytest.mark.asyncio
    async def test_a_symlink_to_a_file_outside_is_not_listed_and_not_readable(self, b, outside):
        await b.s.write_bytes(b.uri("real.txt"), b"in")
        os.symlink(outside, b.path("leak.txt"))
        listed = {o.storage_uri for o in await b.s.list_objects(b.uri(""))}
        assert listed == {b.uri("real.txt")}
        with pytest.raises(ValidationError):
            await b.s.read_bytes(b.uri("leak.txt"))

    @pytest.mark.asyncio
    async def test_a_symlinked_directory_outside_is_not_listed(self, b, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "x.txt").write_bytes(b"x")
        await b.s.write_bytes(b.uri("real.txt"), b"in")
        os.symlink(elsewhere, b.path("linkdir"))
        listed = {o.storage_uri for o in await b.s.list_objects(b.uri(""))}
        assert listed == {b.uri("real.txt")}


# --- listing --------------------------------------------------------------------------------


class TestListing:
    @pytest_asyncio.fixture
    async def seeded(self, b):
        for key in ("run-1/a.txt", "run-1/sub/b.txt", "run-10/c.txt", "run-1x.txt", "top.txt"):
            await b.s.write_bytes(b.uri(key), key.encode())
        return b

    @pytest.mark.asyncio
    async def test_prefixes_match_literally_and_results_are_sorted(self, seeded):
        b = seeded
        keys = [o.storage_uri for o in await b.s.list_objects(b.uri("run-1"))]
        assert keys == [b.uri(k) for k in ("run-1/a.txt", "run-1/sub/b.txt", "run-10/c.txt", "run-1x.txt")]
        keys = [o.storage_uri for o in await b.s.list_objects(b.uri("run-1/"))]
        assert keys == [b.uri("run-1/a.txt"), b.uri("run-1/sub/b.txt")]

    @pytest.mark.asyncio
    async def test_nonrecursive_listing_stops_at_the_next_separator(self, seeded):
        b = seeded
        keys = [o.storage_uri for o in await b.s.list_objects(b.uri("run-1/"), recursive=False)]
        assert keys == [b.uri("run-1/a.txt")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit,expected", [(0, 0), (-1, 0), (1, 1), (2, 2), (None, 5)])
    async def test_limits(self, seeded, limit, expected):
        b = seeded
        assert len(await b.s.list_objects(b.uri(""), max_results=limit)) == expected

    @pytest.mark.asyncio
    async def test_a_missing_store_lists_nothing(self, b):
        assert await b.s.list_objects(b.uri("", bucket="nothing-here")) == []

    @pytest.mark.asyncio
    async def test_objects_carry_size_and_filename(self, seeded):
        b = seeded
        (obj,) = await b.s.list_objects(b.uri("run-1/sub/"))
        assert (obj.filename, obj.size_bytes) == ("b.txt", len(b"run-1/sub/b.txt"))


# --- reads ----------------------------------------------------------------------------------


class TestPrefixReads:
    @pytest.mark.asyncio
    async def test_lengths(self, b):
        await b.s.write_bytes(b.uri("f"), b"0123456789")
        assert await b.s.read_prefix(b.uri("f"), 4) == b"0123"
        assert await b.s.read_prefix(b.uri("f"), 10) == b"0123456789"
        assert await b.s.read_prefix(b.uri("f"), 50) == b"0123456789"
        assert await b.s.read_prefix(b.uri("f"), 0) == b""
        with pytest.raises(ValueError):
            await b.s.read_prefix(b.uri("f"), -1)

    @pytest.mark.asyncio
    async def test_missing(self, b):
        with pytest.raises(StorageObjectNotFound):
            await b.s.read_prefix(b.uri("nope"), 4)


class TestObjects:
    @pytest.mark.asyncio
    async def test_empty_unicode_binary_and_overwrite(self, b):
        await b.s.write_bytes(b.uri("deep/er/empty"), b"")
        assert await b.s.read_bytes(b.uri("deep/er/empty")) == b""
        await b.s.write_text(b.uri("ü.txt"), "üñí")
        assert await b.s.read_text(b.uri("ü.txt")) == "üñí"
        blob = bytes(range(256))
        await b.s.write_bytes(b.uri("bin"), blob)
        await b.s.write_bytes(b.uri("bin"), blob[::-1])
        assert await b.s.read_bytes(b.uri("bin")) == blob[::-1]

    @pytest.mark.asyncio
    async def test_missing_objects_name_their_uri(self, b):
        for op in (b.s.read_bytes, b.s.get_object_metadata):
            with pytest.raises(StorageObjectNotFound) as exc:
                await op(b.uri("missing.txt"))
            assert b.uri("missing.txt") in str(exc.value)
        with pytest.raises(StorageObjectNotFound):
            await b.s.download_to_file(b.uri("missing.txt"), io.BytesIO())

    @pytest.mark.asyncio
    async def test_delete_is_idempotent(self, b):
        await b.s.write_bytes(b.uri("x"), b"1")
        await b.s.delete(b.uri("x"))
        await b.s.delete(b.uri("x"))
        assert not await b.s.exists(b.uri("x"))

    @pytest.mark.asyncio
    async def test_a_directory_is_not_an_object(self, b):
        await b.s.write_bytes(b.uri("dir/inside.txt"), b"1")
        assert not await b.s.exists(b.uri("dir"))
        with pytest.raises(StorageObjectNotFound):
            await b.s.read_bytes(b.uri("dir"))
        with pytest.raises(StorageObjectNotFound):
            await b.s.get_object_metadata(b.uri("dir"))
        await b.s.delete(b.uri("dir"))  # a no-op, not a permission error
        assert await b.s.exists(b.uri("dir/inside.txt"))

    @pytest.mark.asyncio
    async def test_deleting_an_internal_symlink_removes_the_link_not_its_target(self, b):
        await b.s.write_bytes(b.uri("target.txt"), b"keep me")
        os.symlink(b.path("target.txt"), b.path("link.txt"))
        await b.s.delete(b.uri("link.txt"))
        assert not os.path.lexists(b.path("link.txt"))
        assert await b.s.read_bytes(b.uri("target.txt")) == b"keep me"


class TestStreams:
    @pytest.mark.asyncio
    async def test_upload_rewinds_a_seekable_stream_and_leaves_it_open(self, b):
        stream = io.BytesIO(b"payload")
        stream.read(3)
        await b.s.upload_file(b.uri("s"), stream)
        assert await b.s.read_bytes(b.uri("s")) == b"payload"
        assert not stream.closed

    @pytest.mark.asyncio
    async def test_upload_from_a_non_seekable_stream_reads_from_its_position(self, b):
        class Pipe(io.RawIOBase):
            def __init__(self, data):
                self._data = io.BytesIO(data)

            def readable(self):
                return True

            def seekable(self):
                return False

            def seek(self, *a):
                raise io.UnsupportedOperation("seek")

            def readinto(self, buf):
                chunk = self._data.read(len(buf))
                buf[: len(chunk)] = chunk
                return len(chunk)

        await b.s.upload_file(b.uri("p"), io.BufferedReader(Pipe(b"streamed")))
        assert await b.s.read_bytes(b.uri("p")) == b"streamed"

    @pytest.mark.asyncio
    async def test_download_writes_at_the_callers_position_and_leaves_it_open(self, b):
        await b.s.write_bytes(b.uri("d"), b"body")
        sink = io.BytesIO()
        sink.write(b"head:")
        await b.s.download_to_file(b.uri("d"), sink)
        assert sink.getvalue() == b"head:body"
        assert not sink.closed

    @pytest.mark.asyncio
    async def test_filename_transfers(self, b, tmp_path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"\x00file")
        await b.s.upload_filename(b.uri("u/f.bin"), str(src))
        dest = tmp_path / "out" / "nested" / "f.bin"
        await b.s.download_to_filename(b.uri("u/f.bin"), str(dest))
        assert dest.read_bytes() == b"\x00file"


class TestCopyMove:
    @pytest.mark.asyncio
    async def test_copy_returns_the_destination_and_keeps_the_source(self, b):
        await b.s.write_bytes(b.uri("a"), b"1")
        assert await b.s.copy(b.uri("a"), b.uri("c/b")) == b.uri("c/b")
        assert await b.s.read_bytes(b.uri("c/b")) == b"1"
        assert await b.s.read_bytes(b.uri("a")) == b"1"

    @pytest.mark.asyncio
    async def test_copying_onto_itself_is_a_no_op(self, b):
        await b.s.write_bytes(b.uri("a"), b"1")
        assert await b.s.copy(b.uri("a"), b.uri("a")) == b.uri("a")
        assert await b.s.read_bytes(b.uri("a")) == b"1"

    @pytest.mark.asyncio
    async def test_copy_overwrites_an_existing_destination(self, b):
        await b.s.write_bytes(b.uri("a"), b"new")
        await b.s.write_bytes(b.uri("b"), b"old")
        await b.s.copy(b.uri("a"), b.uri("b"))
        assert await b.s.read_bytes(b.uri("b")) == b"new"

    @pytest.mark.asyncio
    async def test_move_removes_the_source_and_moving_onto_itself_keeps_it(self, b):
        await b.s.write_bytes(b.uri("a"), b"1")
        assert await b.s.move(b.uri("a"), b.uri("m")) == b.uri("m")
        assert not await b.s.exists(b.uri("a"))
        assert await b.s.move(b.uri("m"), b.uri("m")) == b.uri("m")
        assert await b.s.read_bytes(b.uri("m")) == b"1"

    @pytest.mark.asyncio
    async def test_a_failed_move_keeps_the_source(self, b):
        await b.s.write_bytes(b.uri("a"), b"1")
        with pytest.raises(StorageObjectNotFound):
            await b.s.move(b.uri("nope"), b.uri("m"))
        with pytest.raises(ValidationError):
            await b.s.move(b.uri("a"), b.uri("../escape", bucket=".."))
        assert await b.s.read_bytes(b.uri("a")) == b"1"


def test_cloud_prefix_reads_reject_negative_and_skip_zero(monkeypatch):
    """The cloud range request used max(length - 1, 0), so 0 and -1 both asked for byte 0."""
    import asyncio
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("BIOAF_COMPUTE_MODE", "k8s")
    gcs = GcsStorageProvider(org_slug="t")
    gcs._credentials_loaded = True
    client = MagicMock()
    with patch.object(gcs, "_get_gcs_client", return_value=client):
        assert asyncio.run(gcs.read_prefix("gs://b/k", 0)) == b""
        with pytest.raises(ValueError):
            asyncio.run(gcs.read_prefix("gs://b/k", -1))
    client.bucket.assert_not_called()

    s3 = S3StorageProvider(org_slug="t")
    s3._credentials_loaded = True
    s3_client = MagicMock()
    with patch.object(s3, "_get_s3_client", return_value=s3_client):
        assert asyncio.run(s3.read_prefix("s3://b/k", 0)) == b""
        with pytest.raises(ValueError):
            asyncio.run(s3.read_prefix("s3://b/k", -1))
    s3_client.get_object.assert_not_called()


class TestFailuresStayDistinct:
    @pytest.mark.asyncio
    async def test_an_object_that_vanishes_between_the_check_and_the_open_is_not_found(self, b, monkeypatch):
        await b.s.write_bytes(b.uri("v"), b"1")
        os.remove(b.path("v"))
        monkeypatch.setattr(os.path, "isfile", lambda p: True)  # the check still saw it
        with pytest.raises(StorageObjectNotFound):
            await b.s.read_bytes(b.uri("v"))

    @pytest.mark.asyncio
    async def test_a_permission_error_is_not_reported_as_absence(self, b):
        if os.geteuid() == 0:
            pytest.skip("root reads files regardless of mode")
        await b.s.write_bytes(b.uri("locked"), b"1")
        os.chmod(b.path("locked"), 0)
        try:
            with pytest.raises(PermissionError):
                await b.s.read_bytes(b.uri("locked"))
        finally:
            os.chmod(b.path("locked"), 0o600)
