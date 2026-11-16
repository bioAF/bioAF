"""Protected managed copies on NFS (01B.3): the stability mechanism for checksum verification.

A filesystem gives bioAF no snapshot and no way to exclude a producer that can still write the file
it is asked to verify. So when an NFS file has an explicit checksum expectation, the verification
worker copies the producer's completed output into a new, service-owned artifact, publishes it
read-only, hashes that copy, and (on a match) installs its URI as the file's canonical bytes:

    <store>/verified-files/<file_uuid>/<artifact_uuid>/payload

- The artifact directory is created exclusively (mode 0700) and never reused; a later attempt gets a
  new artifact UUID. ``payload.partial`` is created exclusively (0600), filled in bounded chunks,
  fsynced, made read-only, and atomically renamed to ``payload``; the directory is then published
  0555. Nothing is hard-linked: the copy is a new inode.
- Every directory is opened relative to its parent with O_NOFOLLOW, and the source must be a regular
  file opened without following a final symlink, so a substituted link is refused rather than read.
- Free space is checked first (one more full copy plus a reserve).
- Discovery never sees these artifacts (``is_managed_key``); cleanup can remove only them.

The protection only holds when producers cannot write the managed tree. ``preflight`` refuses
checksum-required finalization unless that separation is visible on the filesystem (service-owned,
not group/other writable, no replaceable ancestor) and the configured producer identities differ from
the service's. It does not protect against an administrator or root deliberately changing storage.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import stat
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from app.adapters.models import DIGEST_CHUNK_BYTES, StorageObjectNotFound

MANAGED_PREFIX = "verified-files"
PAYLOAD = "payload"
PARTIAL = "payload.partial"
DEFAULT_RESERVE_BYTES = 1024 * 1024 * 1024

_MANAGED_KEY = re.compile(
    rf"^{MANAGED_PREFIX}/([0-9a-f-]{{36}})/([0-9a-f-]{{36}})/{PAYLOAD}$",
)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ManagedCopyRefused(Exception):
    """The source or destination is not one the managed copy may use (a link, not a regular file,
    an existing artifact directory, or a path outside the managed tree)."""

    code = "managed_copy_refused"


class InsufficientCapacity(Exception):
    code = "nfs_capacity_insufficient"


class SeparationNotEstablished(Exception):
    """Producers could alter the managed tree, so a managed copy would prove nothing."""

    code = "nfs_separation_unavailable"


@dataclass(frozen=True)
class ManagedCopy:
    uri: str
    artifact_uuid: uuid.UUID
    size_bytes: int


def is_managed_key(key: str) -> bool:
    """True for any key inside the managed tree (excluded from output discovery)."""
    return key == MANAGED_PREFIX or key.startswith(f"{MANAGED_PREFIX}/")


def managed_key(file_uuid: uuid.UUID, artifact_uuid: uuid.UUID) -> str:
    return f"{MANAGED_PREFIX}/{file_uuid}/{artifact_uuid}/{PAYLOAD}"


def _open_chain(root: str, parts: Iterable[str], uri: str) -> int:
    """A descriptor for ``root/parts...``, each component opened with O_NOFOLLOW."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError as exc:
                raise StorageObjectNotFound(uri) from exc
            except OSError as exc:  # ELOOP (a link) or ENOTDIR
                raise ManagedCopyRefused(f"{uri} is not reachable without following a link") from exc
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_source(root: str, store: str, key: str, uri: str) -> tuple[int, int]:
    """(descriptor, size) of a regular source file, reached without following any link."""
    segments = [s for s in key.split("/") if s]
    if not segments or any(s in (".", "..") for s in segments):
        raise ManagedCopyRefused(f"{uri} is not a file path")
    parent = _open_chain(root, [store, *segments[:-1]], uri)
    try:
        try:
            fd = os.open(segments[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError as exc:
            raise StorageObjectNotFound(uri) from exc
        except OSError as exc:
            raise ManagedCopyRefused(f"{uri} is a link or cannot be opened") from exc
    finally:
        os.close(parent)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise ManagedCopyRefused(f"{uri} is not a regular file")
    return fd, st.st_size


def _free_bytes(fd: int) -> int:
    st = os.fstatvfs(fd)
    return st.f_bavail * st.f_frsize


def _mkdir_open(parent: int, name: str, mode: int, *, exclusive: bool, uri: str) -> int:
    try:
        os.mkdir(name, mode, dir_fd=parent)
    except FileExistsError:
        if exclusive:
            raise ManagedCopyRefused(f"artifact directory for {uri} already exists") from None
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as exc:
        raise ManagedCopyRefused(f"managed directory for {uri} is a link or not a directory") from exc


def create_artifact_dir(root: str, store: str, file_uuid: uuid.UUID, artifact_uuid: uuid.UUID, uri: str) -> int:
    """A descriptor for a freshly created, private (0700) artifact directory."""
    store_fd = _open_chain(root, [store], uri)
    try:
        managed = _mkdir_open(store_fd, MANAGED_PREFIX, 0o755, exclusive=False, uri=uri)
    finally:
        os.close(store_fd)
    try:
        per_file = _mkdir_open(managed, str(file_uuid), 0o755, exclusive=False, uri=uri)
    finally:
        os.close(managed)
    try:
        return _mkdir_open(per_file, str(artifact_uuid), 0o700, exclusive=True, uri=uri)
    finally:
        os.close(per_file)


def _copy_chunk(src: int, dst: int, n: int) -> int:
    data = os.read(src, n)
    view = memoryview(data)
    while view:
        written = os.write(dst, view)
        view = view[written:]
    return len(data)


def _discard_artifact(root: str, store: str, file_uuid: uuid.UUID, artifact_uuid: uuid.UUID) -> None:
    try:
        per_file = _open_chain(root, [store, MANAGED_PREFIX, str(file_uuid)], "managed artifact")
    except (StorageObjectNotFound, ManagedCopyRefused):
        return
    try:
        try:
            art = os.open(str(artifact_uuid), _DIR_FLAGS, dir_fd=per_file)
        except FileNotFoundError:
            return
        try:
            os.fchmod(art, 0o700)
            for name in (PAYLOAD, PARTIAL):
                try:
                    os.unlink(name, dir_fd=art)
                except FileNotFoundError:
                    pass
        finally:
            os.close(art)
        try:
            os.rmdir(str(artifact_uuid), dir_fd=per_file)
        except FileNotFoundError:
            pass
    finally:
        os.close(per_file)


async def copy_to_managed(
    root: str,
    *,
    source: tuple[str, str, str],
    dest_store: str,
    file_uuid: uuid.UUID,
    artifact_uuid: uuid.UUID,
    build_uri: Callable[[str, str], str],
    chunk_size: int = DIGEST_CHUNK_BYTES,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    on_progress: Callable[[int], Awaitable[None]] | None = None,
) -> ManagedCopy:
    """Copy a completed source into a new published artifact; on any failure, leave none behind."""
    store, key, source_uri = source
    dest_uri = build_uri(dest_store, managed_key(file_uuid, artifact_uuid))
    src, size = await asyncio.to_thread(open_source, root, store, key, source_uri)
    art: int | None = None
    published = False
    try:
        store_fd = await asyncio.to_thread(_open_chain, root, [dest_store], dest_uri)
        try:
            free = await asyncio.to_thread(_free_bytes, store_fd)
        finally:
            os.close(store_fd)
        if free < size + reserve_bytes:
            raise InsufficientCapacity(f"not enough space for a protected copy of {source_uri}")
        art = await asyncio.to_thread(create_artifact_dir, root, dest_store, file_uuid, artifact_uuid, dest_uri)
        dst = os.open(PARTIAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=art)
        copied = 0
        try:
            while True:
                n = await asyncio.to_thread(_copy_chunk, src, dst, chunk_size)
                if n == 0:
                    break
                copied += n
                if on_progress is not None:
                    await on_progress(copied)
            await asyncio.to_thread(os.fsync, dst)
            os.fchmod(dst, 0o444)
        finally:
            os.close(dst)
        os.rename(PARTIAL, PAYLOAD, src_dir_fd=art, dst_dir_fd=art)
        await asyncio.to_thread(os.fsync, art)
        os.fchmod(art, 0o555)
        published = True
        return ManagedCopy(uri=dest_uri, artifact_uuid=artifact_uuid, size_bytes=copied)
    finally:
        os.close(src)
        if art is not None:
            os.close(art)
            if not published:
                await asyncio.to_thread(_discard_artifact, root, dest_store, file_uuid, artifact_uuid)


def remove_managed_artifact(root: str, store: str, key: str, uri: str) -> None:
    """Delete one managed artifact (idempotent). Refuses anything outside the managed tree."""
    match = _MANAGED_KEY.match(key)
    if match is None:
        raise ManagedCopyRefused(f"{uri} is not a managed artifact")
    _discard_artifact(root, store, uuid.UUID(match.group(1)), uuid.UUID(match.group(2)))


def preflight(root: str, store: str, *, producer_uids: Iterable[int]) -> None:
    """Refuse unless producers visibly cannot alter ``<store>/verified-files``."""
    euid = os.geteuid()
    producers = set(producer_uids)
    if not producers:
        raise SeparationNotEstablished("no NFS producer identities are configured (BIOAF_NFS_PRODUCER_UIDS)")
    if euid in producers:
        raise SeparationNotEstablished("the service shares a producer identity")
    chain = [root, os.path.join(root, store), os.path.join(root, store, MANAGED_PREFIX)]
    stats = []
    for path in chain:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise SeparationNotEstablished(f"{MANAGED_PREFIX} is not provisioned for the {store} store") from None
        if not stat.S_ISDIR(st.st_mode):
            raise SeparationNotEstablished(f"{os.path.basename(path) or path} is not a directory")
        if st.st_uid in producers:
            raise SeparationNotEstablished(f"{os.path.basename(path)} is owned by a producer identity")
        stats.append(st)
    managed = stats[-1]
    if managed.st_uid != euid or managed.st_mode & 0o022:
        raise SeparationNotEstablished(f"{MANAGED_PREFIX} must be owned by the service and not group/other writable")
    for parent, child in zip(stats, stats[1:]):
        if parent.st_mode & 0o022 and not (parent.st_mode & stat.S_ISVTX and child.st_uid == euid):
            raise SeparationNotEstablished("an ancestor of the managed tree lets producers replace it")


def os_error_code(exc: OSError) -> str:
    return "nfs_capacity_insufficient" if exc.errno in (errno.ENOSPC, errno.EDQUOT) else "nfs_io_error"
