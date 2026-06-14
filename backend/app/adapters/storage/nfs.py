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
import shutil
import uuid
from typing import BinaryIO
from urllib.parse import urlparse

from app.adapters.base import StorageProvider
from app.adapters.capabilities import CapabilityNotSupported, ProviderCapabilities
from app.exceptions import ValidationError
from app.adapters.models import (
    BucketMetrics,
    ObjectMetadata,
    StorageMetrics,
    StorageObjectNotFound,
    StorageStore,
    StoredObject,
)

_DEFAULT_ROOT = os.environ.get("BIOAF_NFS_ROOT", "/srv/bioaf")


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write_file_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _write_file_stream(path: str, file_obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if hasattr(file_obj, "seek"):
        try:
            file_obj.seek(0)
        except (OSError, ValueError):
            pass
    with open(path, "wb") as f:
        shutil.copyfileobj(file_obj, f)


def _stream_file_into(path: str, file_obj) -> None:
    with open(path, "rb") as f:
        shutil.copyfileobj(f, file_obj)


def _copy_file(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.copy2(src, dest)


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

    def __init__(self, root: str | None = None):
        self._root = root or _DEFAULT_ROOT

    def capabilities(self) -> ProviderCapabilities:
        """A filesystem cannot mint signed URLs and has no storage tiers."""
        return ProviderCapabilities(signed_url_upload=False, storage_tier_metrics=False)

    # -- URI <-> path mapping (traversal-confined) ----------------------------

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse ``file://<store>/<key>`` into (store, key)."""
        if not uri.startswith("file://"):
            raise ValidationError(f"Not an NFS storage URI: {uri!r}")
        parsed = urlparse(uri)
        return parsed.netloc, parsed.path.lstrip("/")

    def _store_root(self, store: str) -> str:
        return os.path.join(self._root, store)

    def _path(self, uri: str) -> str:
        """Resolve a URI to a concrete path, confined under the mount root.

        Rejects any key that would escape the root (path traversal), so a crafted
        ``file://working/../../etc/passwd`` cannot read or write outside the mount.
        """
        store, key = self._parse_uri(uri)
        # Reject traversal in the user-derived key first (the explicit '..'/abs
        # check is a CodeQL-recognised path-injection barrier), then confirm the
        # resolved real path stays under the mount root (the semantic guard).
        if os.path.isabs(key) or ".." in key.split("/"):
            raise ValidationError(f"Storage URI escapes the mount root: {uri!r}")
        base = os.path.realpath(self._root)
        full = os.path.realpath(os.path.join(base, store, key))
        if full != base and not full.startswith(base + os.sep):
            raise ValidationError(f"Storage URI escapes the mount root: {uri!r}")
        return full

    # -- pipeline staging -----------------------------------------------------

    async def resolve_input_path(self, file_record: dict) -> str:
        uri = file_record.get("storage_uri") or file_record.get("gcs_uri")
        if uri and uri.startswith("file://"):
            return self._path(uri)
        filename = file_record.get("filename", "unknown")
        return os.path.join(self._root, "inputs", filename)

    async def resolve_output_path(self, pipeline_run: dict, filename: str) -> str:
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        return os.path.join(
            self._root, "results", "experiments", str(experiment_id), "pipeline-runs", str(run_id), filename
        )

    async def stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        return await asyncio.to_thread(self._stage_inputs, file_records, working_dir)

    def _stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        os.makedirs(working_dir, exist_ok=True)
        staged_paths = []
        for record in file_records:
            filename = record.get("filename", f"file-{uuid.uuid4().hex[:8]}")
            src = record.get("local_path")
            if not src:
                uri = record.get("storage_uri") or record.get("gcs_uri")
                if uri and uri.startswith("file://"):
                    src = self._path(uri)
            dest = os.path.join(working_dir, filename)
            if src and os.path.exists(src):
                shutil.copy2(src, dest)
            else:
                with open(dest, "w") as f:
                    f.write(f"# placeholder for {filename}\n")
            staged_paths.append(dest)
        return staged_paths

    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[StoredObject]:
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        return await asyncio.to_thread(self._collect_outputs, working_dir, run_id, experiment_id)

    def _collect_outputs(self, working_dir: str, run_id, experiment_id) -> list[StoredObject]:
        collected: list[StoredObject] = []
        if not os.path.isdir(working_dir):
            return collected
        for fname in os.listdir(working_dir):
            src = os.path.join(working_dir, fname)
            if not os.path.isfile(src):
                continue
            key = f"experiments/{experiment_id}/pipeline-runs/{run_id}/{fname}"
            uri = f"file://{StorageStore.RESULTS.value}/{key}"
            _copy_file(src, self._path(uri))
            collected.append(StoredObject(filename=fname, storage_uri=uri, size_bytes=os.path.getsize(src)))
        return collected

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

    def cli_auth_command(self, key_file: str) -> str:
        # NFS is a mounted filesystem; no CLI authentication step is needed.
        return ""

    def cli_copy_in(self, uri: str, local_path: str) -> str:
        return f"cp {self._path(uri)} {local_path}"

    def cli_copy_out(self, local_path: str, uri: str) -> str:
        return f"cp -r {local_path} {self._path(uri)}"

    def staging_image(self) -> str:
        # A mounted filesystem stages by plain `cp`, so it needs only a tiny
        # coreutils image, not a cloud CLI.
        return "busybox:stable"

    def image_storage_pip_packages(self) -> str:
        # A mounted filesystem needs no cloud storage client library.
        return ""

    async def read_text(self, uri: str, *, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(uri)).decode(encoding)

    async def read_bytes(self, uri: str) -> bytes:
        path = self._path(uri)
        if not os.path.exists(path):
            raise StorageObjectNotFound(uri)
        return await asyncio.to_thread(_read_file_bytes, path)

    async def write_text(self, uri: str, text: str, *, content_type: str = "text/plain") -> None:
        await self.write_bytes(uri, text.encode("utf-8"), content_type=content_type)

    async def write_bytes(self, uri: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(_write_file_bytes, self._path(uri), data)

    async def upload_file(self, uri: str, file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        await asyncio.to_thread(_write_file_stream, self._path(uri), file_obj)

    async def upload_filename(self, uri: str, local_path: str, *, content_type: str | None = None) -> None:
        await asyncio.to_thread(_copy_file, local_path, self._path(uri))

    async def download_to_file(self, uri: str, file_obj: BinaryIO) -> None:
        path = self._path(uri)
        if not os.path.exists(path):
            raise StorageObjectNotFound(uri)
        await asyncio.to_thread(_stream_file_into, path, file_obj)

    async def download_to_filename(self, uri: str, local_path: str) -> None:
        src = self._path(uri)
        if not os.path.exists(src):
            raise StorageObjectNotFound(uri)
        await asyncio.to_thread(_copy_file, src, local_path)

    async def delete(self, uri: str, *, generation: int | None = None) -> None:
        # A filesystem has no object generations; ignore ``generation``.
        path = self._path(uri)
        if os.path.exists(path):
            await asyncio.to_thread(os.remove, path)

    async def exists(self, uri: str) -> bool:
        return os.path.exists(self._path(uri))

    async def list_objects(
        self,
        uri_prefix: str,
        *,
        recursive: bool = True,
        include_versions: bool = False,
        max_results: int | None = None,
    ) -> list[StoredObject]:
        return await asyncio.to_thread(self._list_objects, uri_prefix, max_results)

    def _list_objects(self, uri_prefix: str, max_results: int | None) -> list[StoredObject]:
        store, key_prefix = self._parse_uri(uri_prefix)
        store_root = self._store_root(store)
        results: list[StoredObject] = []
        if not os.path.isdir(store_root):
            return results
        for dirpath, _dirs, files in os.walk(store_root):
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, store_root).replace(os.sep, "/")
                if key_prefix and not rel.startswith(key_prefix):
                    continue
                results.append(
                    StoredObject(
                        filename=fname,
                        storage_uri=f"file://{store}/{rel}",
                        size_bytes=os.path.getsize(full),
                    )
                )
                if max_results is not None and len(results) >= max_results:
                    return results
        return results

    async def copy(self, source_uri: str, dest_uri: str) -> str:
        await asyncio.to_thread(self._copy, source_uri, dest_uri)
        return dest_uri

    def _copy(self, source_uri: str, dest_uri: str) -> None:
        src = self._path(source_uri)
        if not os.path.exists(src):
            raise StorageObjectNotFound(source_uri)
        _copy_file(src, self._path(dest_uri))

    async def move(self, source_uri: str, dest_uri: str) -> str:
        await asyncio.to_thread(self._move, source_uri, dest_uri)
        return dest_uri

    def _move(self, source_uri: str, dest_uri: str) -> None:
        # Copy-verify-delete: leave the source intact if the copy fails.
        self._copy(source_uri, dest_uri)
        if not os.path.exists(self._path(dest_uri)):
            raise RuntimeError(f"Move verification failed: {dest_uri!r} not written")
        os.remove(self._path(source_uri))

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        path = self._path(uri)
        if not os.path.exists(path):
            raise StorageObjectNotFound(uri)
        size = await asyncio.to_thread(os.path.getsize, path)
        return ObjectMetadata(uri=uri, size_bytes=size)

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
