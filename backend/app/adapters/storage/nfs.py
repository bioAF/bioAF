"""NFS storage adapter: a shared-filesystem backend for the BAL StorageProvider.

For on-premise / SLURM sites that keep pipeline inputs/outputs and platform data
on a shared filesystem (NFS mount) rather than an object store. It implements the
full Phase 3 object-store interface against files rooted at a configured mount,
mapping each logical store (StorageStore) to a subdirectory.

A filesystem cannot mint signed URLs, so this backend declares
``signed_url_upload=False`` (the capability-aware UI then hides direct upload and
uses the server-proxied path) and ``generate_signed_url`` /
``create_resumable_upload_url`` raise ``CapabilityNotSupported``. Every path
operation is confined under the mount root (no traversal). URIs are
``file://<store>/<key>`` (opaque storage URIs, like ``gs://...`` for GCS).
"""

from __future__ import annotations

import asyncio
import os
from typing import BinaryIO

from app.adapters.base import StorageProvider
from app.adapters.capabilities import CapabilityNotSupported, ProviderCapabilities
from app.exceptions import ValidationError
from app.adapters.storage import nfs_managed_copy
from app.adapters.storage.digest import ordered_md5
from app.adapters.storage.local_fs import LocalObjectFilesystem
from app.adapters.storage.uri import split_storage_uri
from app.platform.run_output_layout import directory_prefix
from app.platform.shell_args import ShellExpr, absolute_path, shell_join, shell_word
from app.adapters.models import (
    DIGEST_CHUNK_BYTES,
    ContainerObservation,
    ContainerPresence,
    ObjectDigest,
    ObjectIdentity,
    BucketMetrics,
    ObjectMetadata,
    validate_object_metadata,
    StorageMetrics,
    StorageStore,
    StoredObject,
)

_DEFAULT_ROOT = os.environ.get("BIOAF_NFS_ROOT", "/srv/bioaf")


def _dir_size_and_count(path: str) -> tuple[int, int]:
    total = 0
    count = 0
    if not os.path.isdir(path):
        return (0, 0)
    for dirpath, _dirs, files in os.walk(path):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
                count += 1
            except OSError:
                pass
    return (total, count)


class NfsStorageProvider(StorageProvider):
    """Shared-filesystem (NFS) storage backend."""

    # Each store is a directory named after the store, not a configured bucket.
    named_containers = False

    def __init__(self, root: str | None = None):
        self._root = root or _DEFAULT_ROOT

    def capabilities(self) -> ProviderCapabilities:
        """A filesystem cannot mint signed URLs and has no storage tiers."""
        return ProviderCapabilities(signed_url_upload=False, storage_tier_metrics=False)

    def compute_job_upload_refusal(self) -> str | None:
        # Kubernetes is the only compute backend that runs pipeline jobs, and it mounts no NFS
        # volume. A directory existing inside the job proves nothing: it may be the node's own disk.
        return (
            "Pipeline outputs cannot be saved to NFS storage: NFS is not mounted into pipeline jobs, "
            "so the outputs would be lost when the job ends."
        )

    # -- URI <-> path mapping (traversal-confined) ----------------------------

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse ``file://<store>/<key>`` into (store, key)."""
        return split_storage_uri(uri, "file", label="NFS storage")

    def _store_root(self, store: str) -> str:
        return os.path.join(self._root, store)

    @property
    def _fs(self) -> LocalObjectFilesystem:
        return LocalObjectFilesystem(self._root, self._parse_uri, self.build_uri)

    def _path(self, uri: str) -> str:
        """Resolve a URI to a concrete path, confined under the mount root.

        Rejects a store or key that would escape the root (path traversal, a symlink out), so a
        crafted ``file://working/../../etc/passwd`` cannot read or write outside the mount. A URI
        ending in ``/`` names a directory.
        """
        return self._fs.path(uri, directory=uri.endswith("/"))

    # -- pipeline output collection ------------------------------------------

    async def collect_outputs(self, output_uri: str, pipeline_run: dict) -> list[StoredObject]:
        """Every object under the run's output directory URI, with its canonical, readable URI.

        Discovery only: nothing is copied. The directory is listed with a trailing separator, so
        run 1 never collects runs 10 or 100; directory placeholder objects are skipped, and so are
        protected verification copies (``verified-files/``), which are never outputs to register.
        """
        self._parse_uri(output_uri)
        objects = await self.list_objects(directory_prefix(output_uri))
        return [
            o for o in objects if o.filename and not nfs_managed_copy.is_managed_key(self._parse_uri(o.storage_uri)[1])
        ]

    async def get_storage_metrics(self) -> StorageMetrics:
        return await asyncio.to_thread(self._storage_metrics)

    def _storage_metrics(self) -> StorageMetrics:
        buckets: list[BucketMetrics] = []
        total_bytes = 0
        for store in StorageStore:
            size, count = _dir_size_and_count(self._store_root(store.value))
            total_bytes += size
            buckets.append(
                BucketMetrics(
                    name=store.value,
                    size_gb=round(size / 1e9, 6),
                    object_count=count,
                    storage_class=None,
                    cost_monthly_usd=0.0,
                )
            )
        fs: dict = {}
        try:
            st = os.statvfs(self._root)
            fs = {
                "total_bytes": st.f_blocks * st.f_frsize,
                "free_bytes": st.f_bavail * st.f_frsize,
            }
        except OSError:
            pass
        return StorageMetrics(
            buckets=buckets,
            total_size_gb=round(total_bytes / 1e9, 6),
            total_cost_monthly_usd=0.0,
            provider_details={"filesystem": fs, "root": self._root},
        )

    # -- object-store interface ----------------------------------------------

    async def resolve_uri(self, store: StorageStore, key: str) -> str:
        return f"file://{store.value}/{key.lstrip('/')}"

    def build_uri(self, bucket: str, key: str) -> str:
        return f"file://{bucket}/{key.lstrip('/')}"

    def parse_uri(self, uri: str) -> tuple[str, str]:
        return self._parse_uri(uri)

    # A mounted filesystem copies with plain `cp`. Every remote operand is a `file://` URI mapped
    # through the confined `_path()` (so traversal is refused before a command exists), then
    # quoted with the local operands as literal arguments. A URI ending in `/` names a directory.

    def _operand(self, uri: str) -> str:
        if isinstance(uri, ShellExpr):
            raise ValidationError("An NFS operand must be a file:// URI the adapter can map")
        path = self._path(uri)
        return path + "/" if uri.endswith("/") else path

    def _mounted_or_fail(self) -> str:
        root = shell_word(os.path.realpath(self._root))
        message = shell_word(f"NFS root {self._root} is not mounted here")
        return f"if [ ! -d {root} ]; then echo {message} >&2; exit 1; fi"

    def cli_auth_command(self, key_file: str) -> str:
        # NFS is a mounted filesystem; no CLI authentication step is needed.
        return ""

    def cli_copy_in(self, uri: str, local_path: str) -> str:
        return shell_join(["cp", self._operand(uri), absolute_path(local_path)])

    def cli_copy_out(self, local_path: str, uri: str) -> str:
        dest = self._operand(uri)
        return f"mkdir -p {shell_word(os.path.dirname(dest.rstrip('/')))} && " + shell_join(
            ["cp", "-r", absolute_path(local_path), dest]
        )

    def cli_copy_out_file(self, local_path: str, uri: str) -> str:
        dest = self._operand(uri)
        parent = dest.rstrip("/") if dest.endswith("/") else os.path.dirname(dest)
        return f"mkdir -p {shell_word(parent)} && " + shell_join(["cp", absolute_path(local_path), dest])

    def cli_copy_out_contents(self, local_dir: str, uri: str) -> str:
        # `DIR/.` is the directory's contents (dotfiles included), not a glob. A root this machine
        # cannot see fails the copy rather than `mkdir -p` creating it on local disk, where the
        # outputs would be reported saved and then lost. A plain failing test, not `exit`, so a
        # caller's `||` still runs and its own exit status rules.
        dest = self._operand(uri).rstrip("/")
        source = shell_word(absolute_path(local_dir).rstrip("/") or "/")
        root = shell_word(os.path.realpath(self._root))
        return f"[ -d {root} ] && mkdir -p {shell_word(dest)} && cp -r {source}/. {shell_word(dest)}/"

    def sync_in_command(self, remote_prefix: str, local_dir: str) -> list[str]:
        # A missing prefix (nothing saved yet) is tolerated; an unmounted root is not, because
        # reporting success there would claim data was restored when the store was never visible.
        src = shell_word(self._operand(remote_prefix).rstrip("/"))
        dest = shell_word(absolute_path(local_dir))
        return ["/bin/sh", "-c", f"{self._mounted_or_fail()}; if [ -d {src} ]; then cp -r {src}/. {dest}; fi"]

    def sync_out_command(self, local_dir: str, remote_prefix: str) -> list[str]:
        src = shell_word(absolute_path(local_dir).rstrip("/") or "/")
        dest = shell_word(self._operand(remote_prefix).rstrip("/"))
        return ["/bin/sh", "-c", f"{self._mounted_or_fail()}; mkdir -p {dest} && cp -r {src}/. {dest}"]

    def staging_image(self) -> str:
        # A mounted filesystem stages by plain `cp`, so it needs only a tiny
        # coreutils image, not a cloud CLI.
        return "busybox:stable"

    def nextflow_scratch_directives(self, work_dir: str) -> list[str]:
        # A mounted POSIX filesystem is the workDir directly; no Fusion overlay.
        return [f"workDir = '{work_dir}'"]

    def image_storage_pip_packages(self) -> str:
        # A mounted filesystem needs no cloud storage client library.
        return ""

    async def read_text(self, uri: str, *, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(uri)).decode(encoding)

    async def read_bytes(self, uri: str) -> bytes:
        return await asyncio.to_thread(self._fs.read_bytes, uri)

    async def read_prefix(self, uri: str, length: int) -> bytes:
        return await asyncio.to_thread(self._fs.read_prefix, uri, length)

    async def write_text(self, uri: str, text: str, *, content_type: str = "text/plain") -> None:
        await self.write_bytes(uri, text.encode("utf-8"), content_type=content_type)

    async def write_bytes(self, uri: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self._fs.write_bytes, uri, data)

    async def upload_file(
        self,
        uri: str,
        file_obj: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        metadata = validate_object_metadata(metadata)
        await asyncio.to_thread(self._fs.write_stream, uri, file_obj, metadata)

    async def upload_filename(
        self,
        uri: str,
        local_path: str,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        metadata = validate_object_metadata(metadata)
        await asyncio.to_thread(self._fs.upload_filename, uri, local_path, metadata)

    async def download_to_file(self, uri: str, file_obj: BinaryIO) -> None:
        await asyncio.to_thread(self._fs.stream_into, uri, file_obj)

    async def download_to_filename(self, uri: str, local_path: str) -> None:
        await asyncio.to_thread(self._fs.download_to_filename, uri, local_path)

    async def delete(self, uri: str, *, generation: int | None = None) -> None:
        # A filesystem has no object generations; ignore ``generation``. A protected managed copy is
        # published read-only, so it is removed by the managed-artifact procedure, never a bare unlink.
        if self._is_managed(uri):
            await self.remove_managed_artifact(uri)
            return
        await asyncio.to_thread(self._fs.delete, uri)

    def _is_managed(self, uri: str) -> bool:
        return nfs_managed_copy.is_managed_key(self._parse_uri(uri)[1])

    async def exists(self, uri: str) -> bool:
        return await asyncio.to_thread(self._fs.exists, uri)

    async def list_objects(
        self,
        uri_prefix: str,
        *,
        recursive: bool = True,
        include_versions: bool = False,
        max_results: int | None = None,
    ) -> list[StoredObject]:
        return await asyncio.to_thread(self._fs.list, uri_prefix, recursive=recursive, max_results=max_results)

    async def copy(self, source_uri: str, dest_uri: str) -> str:
        await asyncio.to_thread(self._fs.copy, source_uri, dest_uri)
        return dest_uri

    async def move(self, source_uri: str, dest_uri: str) -> str:
        # Copy-verify-delete: leave the source intact if the copy fails.
        if self._is_managed(source_uri):
            if await asyncio.to_thread(self._copy_managed_for_move, source_uri, dest_uri):
                await self.remove_managed_artifact(source_uri)
            return dest_uri
        await asyncio.to_thread(self._fs.move, source_uri, dest_uri)
        return dest_uri

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        size = await asyncio.to_thread(self._fs.size, uri)
        return ObjectMetadata(uri=uri, size_bytes=size, metadata=await asyncio.to_thread(self._fs.read_metadata, uri))

    async def object_identity(self, uri: str) -> ObjectIdentity:
        # A filesystem has no provider digest: an expectation always streams (from the protected
        # managed copy once 01B.3's finalization applies).
        size, version = await asyncio.to_thread(self._fs.identity, uri)
        return ObjectIdentity(uri=uri, size_bytes=size, version=version)

    async def stream_md5(
        self, identity: ObjectIdentity, *, chunk_size: int = DIGEST_CHUNK_BYTES, on_progress=None
    ) -> ObjectDigest:
        read, close = await asyncio.to_thread(self._fs.open_version, identity.uri, identity.version)
        return await ordered_md5(
            read,
            close,
            uri=identity.uri,
            version=identity.version,
            expected_size=identity.size_bytes,
            chunk_size=chunk_size,
            on_progress=on_progress,
        )

    # -- protected managed copies (01B.3, app/adapters/storage/nfs_managed_copy.py) -----------

    async def prepare_managed_copy(
        self,
        source_uri: str,
        *,
        dest_store: str,
        file_uuid,
        artifact_uuid,
        chunk_size: int = DIGEST_CHUNK_BYTES,
        reserve_bytes: int = nfs_managed_copy.DEFAULT_RESERVE_BYTES,
        on_progress=None,
    ) -> nfs_managed_copy.ManagedCopy:
        """Copy a completed output into a new read-only service-owned artifact (see the module)."""
        self._fs.path(source_uri)  # confinement and URI shape, before any open
        store, key = self._parse_uri(source_uri)
        return await nfs_managed_copy.copy_to_managed(
            self._root,
            source=(store, key, source_uri),
            dest_store=dest_store,
            file_uuid=file_uuid,
            artifact_uuid=artifact_uuid,
            build_uri=self.build_uri,
            chunk_size=chunk_size,
            reserve_bytes=reserve_bytes,
            on_progress=on_progress,
        )

    def _copy_managed_for_move(self, source_uri: str, dest_uri: str) -> bool:
        """The copy half of moving a protected copy, with the filesystem move's own checks: both URIs
        confined, and a move onto the same file is a no-op (False: nothing to remove afterwards)."""
        src = self._fs.path(source_uri)
        dest = self._fs.path(dest_uri)
        if os.path.exists(dest) and os.path.samefile(src, dest):
            return False
        self._fs.copy(source_uri, dest_uri)
        if not os.path.isfile(dest):
            raise RuntimeError(f"Move verification failed: {dest_uri!r} not written")
        return True

    async def remove_managed_artifact(self, uri: str) -> None:
        self._fs.path(uri)  # the same container/key confinement as every other operation, first
        store, key = self._parse_uri(uri)
        await asyncio.to_thread(nfs_managed_copy.remove_managed_artifact, self._root, store, key, uri)

    def managed_preflight(self, store: str, producer_uids) -> None:
        nfs_managed_copy.preflight(self._root, store, producer_uids=producer_uids)

    async def inspect_container(self, uri: str) -> ContainerObservation:
        # The export root must itself be a mount point: a plain local directory at
        # that path (an unmounted fallback) is not the shared store. Nothing is
        # created; a store subdirectory appears on first write.
        mounted = await asyncio.to_thread(os.path.ismount, os.path.realpath(self._root))
        if not mounted:
            return ContainerObservation(presence=ContainerPresence.NOT_MOUNTED)
        return ContainerObservation(presence=ContainerPresence.PRESENT, limits=("identity",))

    async def get_bucket_info(self, uri: str) -> dict:
        # A filesystem store has no object versioning.
        return {"versioning_enabled": False}

    async def generate_signed_url(
        self,
        uri: str,
        *,
        method: str = "GET",
        expiry_seconds: int = 3600,
        content_type: str | None = None,
    ) -> str:
        raise CapabilityNotSupported(
            "signed_url_upload",
            "The NFS storage backend cannot mint signed URLs; use the proxied upload path.",
        )

    async def sign_download(self, uri: str, *, max_ttl_seconds: int, min_ttl_seconds: int):
        raise CapabilityNotSupported(
            "signed_url_upload", "The NFS storage backend cannot mint signed links; use direct download."
        )

    async def check_signed_download(self, *, min_ttl_seconds: int) -> None:
        await self.sign_download("", max_ttl_seconds=min_ttl_seconds, min_ttl_seconds=min_ttl_seconds)

    async def create_resumable_upload_url(
        self,
        uri: str,
        *,
        content_type: str = "application/octet-stream",
        size_bytes: int | None = None,
        origin: str | None = None,
    ) -> str:
        raise CapabilityNotSupported(
            "signed_url_upload",
            "The NFS storage backend has no direct client upload; use the proxied upload path.",
        )
