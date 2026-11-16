"""01B.3: protected NFS managed copies for checksum verification.

The managed copy is a new inode in a fresh, exclusively created, service-owned directory under
``<store>/verified-files/<file_uuid>/<artifact_uuid>/``; it is published read-only and its bytes are
what verification hashes. These tests use the real filesystem. The UID-separation test runs a real
second identity through passwordless sudo when the host allows it (CI does); elsewhere it is skipped
and says so, because a same-user harness cannot prove the boundary.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from app.adapters.models import ObjectChanged, StorageObjectNotFound
from app.adapters.storage import nfs_managed_copy
from app.adapters.storage.nfs import NfsStorageProvider
from app.adapters.storage.nfs_managed_copy import (
    InsufficientCapacity,
    ManagedCopyRefused,
    SeparationNotEstablished,
)

pytestmark = pytest.mark.asyncio

PAYLOAD = b"@r1\nACGT\n+\nIIII\n" * 4096
SOURCE = "file://raw/runs/7/out/a.fastq.gz"


@pytest.fixture
def nfs(tmp_path) -> NfsStorageProvider:
    adapter = NfsStorageProvider(root=str(tmp_path))
    return adapter


async def _write_source(nfs, data=PAYLOAD, uri=SOURCE):
    await nfs.write_bytes(uri, data)
    return uri


async def _copy(nfs, uri=SOURCE, *, artifact=None, file_uuid=None, **kw):
    return await nfs.prepare_managed_copy(
        uri,
        dest_store="raw",
        file_uuid=file_uuid or uuid.UUID(int=1),
        artifact_uuid=artifact or uuid.uuid4(),
        **kw,
    )


async def test_the_copy_is_a_new_read_only_inode_in_a_fresh_directory(nfs, tmp_path):
    await _write_source(nfs)
    artifact = uuid.uuid4()
    progress: list[int] = []

    async def on_progress(done):
        progress.append(done)

    copy = await _copy(nfs, artifact=artifact, on_progress=on_progress)

    assert copy.uri == f"file://raw/verified-files/{uuid.UUID(int=1)}/{artifact}/payload"
    assert copy.size_bytes == len(PAYLOAD)
    path = Path(nfs._fs.path(copy.uri))
    source_path = Path(nfs._fs.path(SOURCE))
    assert path.read_bytes() == PAYLOAD
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o555
    assert path.stat().st_ino != source_path.stat().st_ino and path.stat().st_nlink == 1
    assert not (path.parent / "payload.partial").exists()
    assert source_path.exists()  # the source is retained until verification commits
    assert progress and progress[-1] == len(PAYLOAD)

    # The protected copy is what gets hashed.
    identity = await nfs.object_identity(copy.uri)
    digest = await nfs.stream_md5(identity)
    assert digest.hex == hashlib.md5(PAYLOAD).hexdigest()


async def test_source_changes_after_the_copy_do_not_reach_the_artifact(nfs):
    await _write_source(nfs)
    copy = await _copy(nfs)
    source_path = Path(nfs._fs.path(SOURCE))
    with open(source_path, "r+b") as f:
        f.write(b"XXXX")
    source_path.unlink()
    await _write_source(nfs, b"replaced")  # a new file reusing the source name
    assert Path(nfs._fs.path(copy.uri)).read_bytes() == PAYLOAD


async def test_an_artifact_directory_is_never_reused(nfs):
    await _write_source(nfs)
    artifact = uuid.uuid4()
    await _copy(nfs, artifact=artifact)
    with pytest.raises(ManagedCopyRefused):
        await _copy(nfs, artifact=artifact)


async def test_a_symlinked_source_or_directory_is_refused(nfs, tmp_path):
    outside = tmp_path / "secret"
    outside.write_bytes(b"secret")
    os.makedirs(tmp_path / "raw" / "runs" / "7" / "out", exist_ok=True)
    os.symlink(outside, tmp_path / "raw" / "runs" / "7" / "out" / "link.fastq.gz")
    with pytest.raises((ManagedCopyRefused, ObjectChanged)):
        await _copy(nfs, "file://raw/runs/7/out/link.fastq.gz")

    # A substituted directory component.
    os.makedirs(tmp_path / "elsewhere", exist_ok=True)
    (tmp_path / "elsewhere" / "b.fastq.gz").write_bytes(b"x")
    os.symlink(tmp_path / "elsewhere", tmp_path / "raw" / "runs" / "8")
    with pytest.raises((ManagedCopyRefused, ObjectChanged)):
        await _copy(nfs, "file://raw/runs/8/b.fastq.gz")


async def test_only_regular_files_are_copied(nfs, tmp_path):
    os.makedirs(tmp_path / "raw" / "runs" / "7" / "dir.fastq.gz")
    with pytest.raises((ManagedCopyRefused, StorageObjectNotFound)):
        await _copy(nfs, "file://raw/runs/7/dir.fastq.gz")
    fifo = tmp_path / "raw" / "runs" / "7" / "pipe.fastq.gz"
    os.mkfifo(fifo)
    with pytest.raises(ManagedCopyRefused):
        await _copy(nfs, "file://raw/runs/7/pipe.fastq.gz")


async def test_a_missing_source_is_not_found(nfs):
    with pytest.raises(StorageObjectNotFound):
        await _copy(nfs, "file://raw/runs/none.fastq.gz")


async def test_insufficient_capacity_creates_nothing(nfs, tmp_path, monkeypatch):
    await _write_source(nfs)
    monkeypatch.setattr(nfs_managed_copy, "_free_bytes", lambda fd: len(PAYLOAD) - 1)
    with pytest.raises(InsufficientCapacity):
        await _copy(nfs, reserve_bytes=0)
    assert not (tmp_path / "raw" / "verified-files").exists() or not any(
        (tmp_path / "raw" / "verified-files").rglob("payload*")
    )


async def test_a_failed_copy_leaves_no_partial_artifact(nfs, tmp_path, monkeypatch):
    await _write_source(nfs)
    artifact = uuid.uuid4()
    real = nfs_managed_copy._copy_chunk
    calls = {"n": 0}

    def failing(src, dst, n):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("I/O error")
        return real(src, dst, n)

    monkeypatch.setattr(nfs_managed_copy, "_copy_chunk", failing)
    with pytest.raises(OSError):
        await _copy(nfs, artifact=artifact, chunk_size=1024)
    assert not (tmp_path / "raw" / "verified-files" / str(uuid.UUID(int=1)) / str(artifact)).exists()


async def test_managed_artifacts_are_not_output_discoveries(nfs):
    await _write_source(nfs, uri="file://raw/runs/7/out/a.fastq.gz")
    await _copy(nfs)
    collected = await nfs.collect_outputs("file://raw/", {})
    assert [o.storage_uri for o in collected] == ["file://raw/runs/7/out/a.fastq.gz"]


async def test_cleanup_removes_only_managed_artifacts(nfs):
    await _write_source(nfs)
    copy = await _copy(nfs)
    with pytest.raises(ManagedCopyRefused):
        await nfs.remove_managed_artifact(SOURCE)
    await nfs.remove_managed_artifact(copy.uri)
    assert not await nfs.exists(copy.uri)
    assert await nfs.exists(SOURCE)
    await nfs.remove_managed_artifact(copy.uri)  # idempotent


async def test_ordinary_deletion_removes_a_protected_copy(nfs):
    """The canonical copy of a verified file is deleted through the normal storage API."""
    await _write_source(nfs)
    copy = await _copy(nfs)
    await nfs.delete(copy.uri)
    assert not await nfs.exists(copy.uri)
    assert await nfs.exists(SOURCE)
    await nfs.delete(copy.uri)  # idempotent, like deleting any missing object


async def test_moving_a_protected_copy_removes_its_artifact(nfs):
    await _write_source(nfs)
    copy = await _copy(nfs)
    dest = "file://raw/archive/a.fastq.gz"
    assert await nfs.move(copy.uri, dest) == dest
    assert await nfs.read_bytes(dest) == PAYLOAD
    assert not await nfs.exists(copy.uri)


# --- separation preflight ---------------------------------------------------------------------


def _layout(root: Path, *, store_mode=0o755, managed_mode=0o755, sticky=False):
    (root / "raw" / "verified-files").mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    os.chmod(root / "raw", store_mode | (stat.S_ISVTX if sticky else 0))
    os.chmod(root / "raw" / "verified-files", managed_mode)


async def test_preflight_requires_configured_distinct_producers(tmp_path):
    _layout(tmp_path)
    with pytest.raises(SeparationNotEstablished, match="producer"):
        nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=())
    with pytest.raises(SeparationNotEstablished, match="producer"):
        nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid(),))
    nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid() + 1,))


async def test_preflight_refuses_a_replaceable_managed_tree(tmp_path):
    _layout(tmp_path, store_mode=0o777)  # producers could rename verified-files away
    with pytest.raises(SeparationNotEstablished):
        nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid() + 1,))
    _layout(tmp_path, store_mode=0o777, sticky=True)  # sticky: entries stay their owner's
    nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid() + 1,))
    _layout(tmp_path, managed_mode=0o775)
    with pytest.raises(SeparationNotEstablished):
        nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid() + 1,))


async def test_preflight_requires_the_managed_prefix_to_exist(tmp_path):
    (tmp_path / "raw").mkdir()
    with pytest.raises(SeparationNotEstablished, match="verified-files"):
        nfs_managed_copy.preflight(str(tmp_path), "raw", producer_uids=(os.geteuid() + 1,))


def _can_switch_user() -> bool:
    if os.geteuid() == 0:
        return True
    return (
        shutil.which("sudo") is not None and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
    )


ATTACK = r"""
import os, sys
artifact, directory, inbox = sys.argv[1], sys.argv[2], sys.argv[3]
results = {}
def attempt(name, fn):
    try:
        fn(); results[name] = "allowed"
    except OSError:
        results[name] = "denied"
attempt("inbox_write", lambda: open(os.path.join(inbox, "out.txt"), "wb").write(b"ok"))
attempt("write", lambda: open(artifact, "r+b").write(b"X"))
attempt("chmod", lambda: os.chmod(artifact, 0o666))
attempt("unlink", lambda: os.unlink(artifact))
attempt("replace", lambda: os.rename(os.path.join(inbox, "out.txt"), artifact))
attempt("create", lambda: open(os.path.join(directory, "evil"), "wb"))
attempt("rmdir", lambda: os.rename(directory, directory + ".gone"))
print(results)
"""


@pytest.mark.skipif(not _can_switch_user(), reason="needs root or passwordless sudo to act as a second UID")
async def test_a_producer_identity_cannot_alter_a_published_artifact():
    root = Path(tempfile.mkdtemp(prefix="bioaf-nfs-", dir="/tmp"))
    try:
        os.chmod(root, 0o755)
        (root / "raw" / "verified-files").mkdir(parents=True)
        os.chmod(root / "raw", 0o1777)  # producers share the store root, sticky
        os.chmod(root / "raw" / "verified-files", 0o755)
        inbox = root / "raw" / "inbox"
        inbox.mkdir()
        os.chmod(inbox, 0o777)
        nfs = NfsStorageProvider(root=str(root))
        await nfs.write_bytes(SOURCE, PAYLOAD)
        copy = await _copy(nfs)
        artifact = nfs._fs.path(copy.uri)
        command = ["/usr/bin/env", "python3", "-c", ATTACK, artifact, os.path.dirname(artifact), str(inbox)]
        preexec = None
        if os.geteuid() == 0:
            import pwd

            nobody = pwd.getpwnam("nobody")

            def preexec():
                os.setgroups([])
                os.setgid(nobody.pw_gid)
                os.setuid(nobody.pw_uid)
        else:
            command = ["sudo", "-n", "-u", "nobody", *command]
        proc = subprocess.run(command, capture_output=True, text=True, preexec_fn=preexec)
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout
        results = eval(out)  # noqa: S307 - our own subprocess's dict literal
        assert results["inbox_write"] == "allowed"  # the harness's producer really can write somewhere
        for action in ("write", "chmod", "unlink", "replace", "create", "rmdir"):
            assert results[action] == "denied", action
        assert Path(artifact).read_bytes() == PAYLOAD
    finally:
        for dirpath, dirnames, filenames in os.walk(root):
            os.chmod(dirpath, 0o700)
        shutil.rmtree(root, ignore_errors=True)
