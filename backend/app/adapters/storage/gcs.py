"""GCS storage adapter.

Supports local/mock mode for development and real GCS API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.
"""

import asyncio
import logging
import os
import shutil
import uuid
from typing import BinaryIO
from urllib.parse import urlparse

from app.adapters.base import StorageProvider
from app.adapters.capabilities import ProviderCapabilities
from app.exceptions import ValidationError
from app.adapters.models import (
    BucketAdminMetrics,
    BucketMetrics,
    ObjectMetadata,
    StorageMetrics,
    StorageObjectNotFound,
    StorageStore,
    StoredObject,
)

logger = logging.getLogger("bioaf.adapters.storage.gcs")

LOCAL_DATA_ROOT = os.environ.get("BIOAF_LOCAL_DATA_ROOT", "/tmp/bioaf-data")

# Subdirectory under LOCAL_DATA_ROOT where local mode emulates an object store
# (gs://<bucket>/<key> maps to LOCAL_DATA_ROOT/_objects/<bucket>/<key>).
_LOCAL_OBJECTS_DIR = "_objects"

# platform_config keys credential resolution reads (impersonation-first).
_CREDENTIAL_CONFIG_KEYS = [
    "gcp_credential_source",
    "gcp_service_account_key",
    "gcp_service_account_email",
    "gcp_bootstrap_sa_email",
]

# The logical stores enumerated for storage metrics (parity with the buckets
# the prior GcsStorageService.get_bucket_metrics iterated: BACKUPS is not a
# managed-metrics bucket).
_METRICS_STORES = (
    StorageStore.INGEST,
    StorageStore.RAW,
    StorageStore.WORKING,
    StorageStore.RESULTS,
    StorageStore.REFERENCES,
    StorageStore.LITERATURE,
    StorageStore.CONFIG_BACKUPS,
)


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


class GcsStorageProvider(StorageProvider):
    """GCS storage backend with local mode for development."""

    def __init__(self, org_slug: str = "demo"):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._org_slug = org_slug
        # Lazily-loaded, cached for the adapter's lifetime. Credentials and
        # bucket names are set at deploy time and the registry reconstructs the
        # adapter on restart, so caching them avoids a DB round-trip per op.
        self._credentials = None
        self._credentials_loaded = False
        self._bucket_config: dict[str, str] | None = None

    def capabilities(self) -> ProviderCapabilities:
        """GCS supports signed-URL direct upload and per-tier storage metrics."""
        return ProviderCapabilities(signed_url_upload=True, storage_tier_metrics=True)

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    @property
    def ingest_bucket(self) -> str:
        return f"bioaf-ingest-{self._org_slug}"

    @property
    def raw_bucket(self) -> str:
        return f"bioaf-raw-{self._org_slug}"

    @property
    def working_bucket(self) -> str:
        return f"bioaf-working-{self._org_slug}"

    @property
    def results_bucket(self) -> str:
        return f"bioaf-results-{self._org_slug}"

    @property
    def config_backups_bucket(self) -> str:
        return f"bioaf-config-backups-{self._org_slug}"

    async def resolve_input_path(self, file_record: dict) -> str:
        if self.is_local:
            return f"/data/inputs/{file_record.get('filename', 'unknown')}"
        return f"/data/inputs/{file_record.get('filename', 'unknown')}"

    async def resolve_output_path(self, pipeline_run: dict, filename: str) -> str:
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        if self.is_local:
            return f"{LOCAL_DATA_ROOT}/results/experiments/{experiment_id}/pipeline-runs/{run_id}/{filename}"
        return f"gs://{self.results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}/{filename}"

    async def stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        if self.is_local:
            return await self._local_stage_inputs(file_records, working_dir)
        return await self._gcs_stage_inputs(file_records, working_dir)

    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[StoredObject]:
        items = (
            await self._local_collect_outputs(working_dir, pipeline_run)
            if self.is_local
            else await self._gcs_collect_outputs(working_dir, pipeline_run)
        )
        return [
            StoredObject(
                filename=d["filename"],
                storage_uri=d.get("gcs_uri", ""),
                size_bytes=d.get("size_bytes"),
                md5_hash=d.get("md5_hash"),
                provider_details={
                    k: v for k, v in d.items() if k not in {"filename", "gcs_uri", "size_bytes", "md5_hash"}
                },
            )
            for d in items
        ]

    async def get_storage_metrics(self) -> StorageMetrics:
        d = self._local_storage_metrics() if self.is_local else await self._gcs_storage_metrics()
        return StorageMetrics(
            buckets=[BucketMetrics(**b) for b in d.get("buckets", [])],
            total_size_gb=d.get("total_size_gb", 0.0),
            total_cost_monthly_usd=d.get("total_cost_monthly_usd", 0.0),
        )

    # -- Object-store interface (Phase 3) --
    #
    # URI-first: every op takes a ``gs://bucket/key`` URI. In local mode the URI
    # maps onto LOCAL_DATA_ROOT/_objects/<bucket>/<key>; in GCS mode it goes
    # through the google-cloud-storage client with cached credentials, off the
    # event loop via ``asyncio.to_thread``.

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse ``gs://bucket/key`` into (bucket, key)."""
        if not uri.startswith("gs://"):
            raise ValidationError(f"Not a storage URI: {uri!r}")
        parsed = urlparse(uri)
        return parsed.netloc, parsed.path.lstrip("/")

    def _local_bucket(self, store: StorageStore) -> str:
        return f"bioaf-{store.value}-{self._org_slug}"

    def _local_path(self, uri: str) -> str:
        bucket, key = self._parse_uri(uri)
        # The key can derive from user input, and local mode maps it onto a real
        # filesystem path. Reject traversal in the key (the explicit '..'/abs
        # check is a CodeQL-recognised path-injection barrier), then confirm the
        # resolved real path stays under the local object root.
        if os.path.isabs(key) or ".." in key.split("/"):
            raise ValidationError(f"Storage key escapes the local object root: {key!r}")
        base = os.path.realpath(os.path.join(LOCAL_DATA_ROOT, _LOCAL_OBJECTS_DIR))
        full = os.path.realpath(os.path.join(base, bucket, key))
        if full != base and not full.startswith(base + os.sep):
            raise ValidationError(f"Storage path escapes the local object root: {uri!r}")
        return full

    async def _get_credentials(self):
        """Resolve and cache GCS credentials via a short-lived session.

        Mirrors the credential resolution that previously lived in
        GcsStorageService: impersonation-first for vm_default installs,
        service-account keys for legacy installs, None (ADC) on failure. Cached
        for the adapter's lifetime (Phase 3 owner decision: internal, cached).
        """
        if not self._credentials_loaded:
            self._credentials = await self._load_credentials()
            self._credentials_loaded = True
        return self._credentials

    async def _load_credentials(self):
        from app.database import async_session_factory
        from app.adapters.credentials import credential_injector
        from app.platform.platform_config_service import PlatformConfigService

        async with async_session_factory() as session:
            config = await PlatformConfigService.get_many(session, _CREDENTIAL_CONFIG_KEYS)
        try:
            return credential_injector.load_gcp_credentials(config)
        except Exception as e:  # pragma: no cover - defensive, mirrors prior behavior
            logger.warning("Failed to load GCS credentials from platform_config: %s", e)
            return None

    async def _get_bucket_config(self) -> dict[str, str]:
        """Read and cache the configured bucket name for each logical store."""
        if self._bucket_config is None:
            from app.database import async_session_factory
            from app.platform.platform_config_service import PlatformConfigService

            keys = [f"{s.value}_bucket_name" for s in StorageStore]
            async with async_session_factory() as session:
                self._bucket_config = await PlatformConfigService.get_many(session, keys)
        return self._bucket_config

    async def resolve_uri(self, store: StorageStore, key: str) -> str:
        key = key.lstrip("/")
        if self.is_local:
            return f"gs://{self._local_bucket(store)}/{key}"
        config = await self._get_bucket_config()
        bucket = config.get(f"{store.value}_bucket_name")
        if not bucket or bucket == "null":
            raise ValidationError(f"No bucket configured for store {store.value!r}")
        return f"gs://{bucket}/{key}"

    def build_uri(self, bucket: str, key: str) -> str:
        return f"gs://{bucket}/{key.lstrip('/')}"

    def parse_uri(self, uri: str) -> tuple[str, str]:
        return self._parse_uri(uri)

    def cli_auth_command(self, key_file: str) -> str:
        # gsutil consults ~/.boto before GOOGLE_APPLICATION_CREDENTIALS, which in
        # cloud-sdk:slim picks up the wrong identity even with the SA key mounted.
        # Activate the SA explicitly and use `gcloud storage` (cli_copy_*), which
        # honors the activated account directly.
        return f"gcloud auth activate-service-account --key-file={key_file} --quiet"

    def cli_copy_in(self, uri: str, local_path: str) -> str:
        return f"gcloud storage cp {uri} {local_path}"

    def cli_copy_out(self, local_path: str, uri: str) -> str:
        return f"gcloud storage cp -r {local_path} {uri}"

    def sync_in_command(self, remote_prefix: str, local_dir: str) -> list[str]:
        # `|| true` so a missing/empty prefix does not fail the init container.
        return ["/bin/sh", "-c", f"gsutil -m rsync -r {remote_prefix} {local_dir} || true"]

    def sync_out_command(self, local_dir: str, remote_prefix: str) -> list[str]:
        return ["/bin/sh", "-c", f"gsutil -m rsync -r {local_dir} {remote_prefix}"]

    def staging_image(self) -> str:
        # google/cloud-sdk:slim ships gsutil + gcloud storage for stage in/out.
        return "google/cloud-sdk:slim"

    def input_mount_spec(
        self, *, name: str, bucket: str, mount_path: str, key_prefix: str = ""
    ) -> tuple[dict, dict, dict]:
        # gcsfuse mounts the whole bucket read-only; key_prefix is unused on GCS
        # (the caller mounts at a sub-path). The annotation triggers GKE's
        # gcsfuse CSI sidecar injection.
        volume_mount = {"name": name, "mountPath": mount_path, "readOnly": True}
        volume = {
            "name": name,
            "csi": {
                "driver": "gcsfuse.csi.storage.gke.io",
                "readOnly": True,
                "volumeAttributes": {
                    "bucketName": bucket,
                    "mountOptions": "implicit-dirs,file-cache:max-size-mb:-1",
                    "gcsfuseLoggingSeverity": "warning",
                },
            },
        }
        return volume, volume_mount, {"gke-gcsfuse/volumes": "true"}

    def nextflow_scratch_directives(self, work_dir: str) -> list[str]:
        # Wave + Fusion mount the gs:// workDir as a local filesystem inside each
        # task pod so head and process pods can exchange .command.run scripts.
        return [
            f"workDir = '{work_dir}'",
            "wave.enabled = true",
            "fusion.enabled = true",
            "fusion.exportStorageCredentials = true",
        ]

    def image_storage_pip_packages(self) -> str:
        return "google-cloud-storage==3.11.0 gsutil==5.37"

    def cloud_build_copy_step(self, uri: str, dest: str) -> dict:
        return {"name": "gcr.io/cloud-builders/gsutil", "args": ["cp", uri, dest]}

    async def read_text(self, uri: str, *, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(uri)).decode(encoding)

    async def read_bytes(self, uri: str) -> bytes:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            return await asyncio.to_thread(_read_file_bytes, path)
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_read_bytes, uri, creds)

    async def write_text(self, uri: str, text: str, *, content_type: str = "text/plain") -> None:
        await self.write_bytes(uri, text.encode("utf-8"), content_type=content_type)

    async def write_bytes(self, uri: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        if self.is_local:
            await asyncio.to_thread(_write_file_bytes, self._local_path(uri), data)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_write_bytes, uri, data, content_type, creds)

    async def upload_file(self, uri: str, file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        if self.is_local:
            await asyncio.to_thread(_write_file_stream, self._local_path(uri), file_obj)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_upload_file, uri, file_obj, content_type, creds)

    async def upload_filename(self, uri: str, local_path: str, *, content_type: str | None = None) -> None:
        if self.is_local:
            dest = self._local_path(uri)
            await asyncio.to_thread(_copy_file, local_path, dest)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_upload_filename, uri, local_path, content_type, creds)

    async def download_to_file(self, uri: str, file_obj: BinaryIO) -> None:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            await asyncio.to_thread(_stream_file_into, path, file_obj)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_download_to_file, uri, file_obj, creds)

    async def download_to_filename(self, uri: str, local_path: str) -> None:
        if self.is_local:
            src = self._local_path(uri)
            if not os.path.exists(src):
                raise StorageObjectNotFound(uri)
            await asyncio.to_thread(_copy_file, src, local_path)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_download_to_filename, uri, local_path, creds)

    async def delete(self, uri: str, *, generation: int | None = None) -> None:
        if self.is_local:
            path = self._local_path(uri)
            if os.path.exists(path):
                await asyncio.to_thread(os.remove, path)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_delete, uri, generation, creds)

    async def exists(self, uri: str) -> bool:
        if self.is_local:
            return os.path.exists(self._local_path(uri))
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_exists, uri, creds)

    async def list_objects(
        self,
        uri_prefix: str,
        *,
        recursive: bool = True,
        include_versions: bool = False,
        max_results: int | None = None,
    ) -> list[StoredObject]:
        if self.is_local:
            return await asyncio.to_thread(self._local_list_objects, uri_prefix, max_results)
        creds = await self._get_credentials()
        return await asyncio.to_thread(
            self._gcs_list_objects, uri_prefix, recursive, include_versions, max_results, creds
        )

    async def copy(self, source_uri: str, dest_uri: str) -> str:
        if self.is_local:
            await asyncio.to_thread(self._local_copy, source_uri, dest_uri)
            return dest_uri
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_copy, source_uri, dest_uri, creds)
        return dest_uri

    async def move(self, source_uri: str, dest_uri: str) -> str:
        if self.is_local:
            await asyncio.to_thread(self._local_move, source_uri, dest_uri)
            return dest_uri
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_move, source_uri, dest_uri, creds)
        return dest_uri

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            size = await asyncio.to_thread(os.path.getsize, path)
            return ObjectMetadata(uri=uri, size_bytes=size)
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_get_object_metadata, uri, creds)

    async def get_bucket_info(self, uri: str) -> dict:
        if self.is_local:
            # The local filesystem store has no object versioning.
            return {"versioning_enabled": False}
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_get_bucket_info, uri, creds)

    async def generate_signed_url(
        self,
        uri: str,
        *,
        method: str = "GET",
        expiry_seconds: int = 3600,
        content_type: str | None = None,
    ) -> str:
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_generate_signed_url, uri, method, expiry_seconds, content_type, creds)

    async def create_resumable_upload_url(
        self,
        uri: str,
        *,
        content_type: str = "application/octet-stream",
        size_bytes: int | None = None,
        origin: str | None = None,
    ) -> str:
        creds = await self._get_credentials()
        return await asyncio.to_thread(
            self._gcs_create_resumable_upload_url, uri, content_type, size_bytes, origin, creds
        )

    # -- Local-mode object-store helpers --

    def _local_list_objects(self, uri_prefix: str, max_results: int | None) -> list[StoredObject]:
        bucket, key_prefix = self._parse_uri(uri_prefix)
        bucket_root = os.path.join(LOCAL_DATA_ROOT, _LOCAL_OBJECTS_DIR, bucket)
        results: list[StoredObject] = []
        if not os.path.isdir(bucket_root):
            return results
        for dirpath, _dirs, files in os.walk(bucket_root):
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, bucket_root)
                if key_prefix and not rel.startswith(key_prefix):
                    continue
                results.append(
                    StoredObject(
                        filename=fname,
                        storage_uri=f"gs://{bucket}/{rel}",
                        size_bytes=os.path.getsize(full),
                    )
                )
                if max_results is not None and len(results) >= max_results:
                    return results
        return results

    def _local_copy(self, source_uri: str, dest_uri: str) -> None:
        src = self._local_path(source_uri)
        if not os.path.exists(src):
            raise StorageObjectNotFound(source_uri)
        dest = self._local_path(dest_uri)
        _copy_file(src, dest)

    def _local_move(self, source_uri: str, dest_uri: str) -> None:
        self._local_copy(source_uri, dest_uri)
        os.remove(self._local_path(source_uri))

    # -- GCS-mode object-store helpers (run inside asyncio.to_thread) --

    def _gcs_read_bytes(self, uri: str, creds) -> bytes:
        from google.api_core.exceptions import NotFound

        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        try:
            return blob.download_as_bytes()
        except NotFound as e:
            raise StorageObjectNotFound(uri) from e

    def _gcs_write_bytes(self, uri: str, data: bytes, content_type: str, creds) -> None:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        blob.upload_from_string(data, content_type=content_type)

    def _gcs_upload_file(self, uri: str, file_obj, content_type, creds) -> None:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        blob.upload_from_file(file_obj, content_type=content_type, rewind=True)

    def _gcs_upload_filename(self, uri: str, local_path: str, content_type, creds) -> None:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        blob.upload_from_filename(local_path, content_type=content_type)

    def _gcs_download_to_file(self, uri: str, file_obj, creds) -> None:
        from google.api_core.exceptions import NotFound

        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        try:
            blob.download_to_file(file_obj)
        except NotFound as e:
            raise StorageObjectNotFound(uri) from e

    def _gcs_download_to_filename(self, uri: str, local_path: str, creds) -> None:
        from google.api_core.exceptions import NotFound

        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            blob.download_to_filename(local_path)
        except NotFound as e:
            raise StorageObjectNotFound(uri) from e

    def _gcs_delete(self, uri: str, generation, creds) -> None:
        from google.api_core.exceptions import NotFound

        bucket, key = self._parse_uri(uri)
        gcs_bucket = self._get_gcs_client(creds).bucket(bucket)
        blob = gcs_bucket.blob(key, generation=generation) if generation is not None else gcs_bucket.blob(key)
        try:
            blob.delete()
        except NotFound:
            pass  # idempotent

    def _gcs_exists(self, uri: str, creds) -> bool:
        bucket, key = self._parse_uri(uri)
        return self._get_gcs_client(creds).bucket(bucket).blob(key).exists()

    def _gcs_list_objects(
        self, uri_prefix: str, recursive: bool, include_versions: bool, max_results: int | None, creds
    ) -> list[StoredObject]:
        bucket, key_prefix = self._parse_uri(uri_prefix)
        client = self._get_gcs_client(creds)
        kwargs: dict = {"prefix": key_prefix}
        if not recursive:
            kwargs["delimiter"] = "/"
        if include_versions:
            kwargs["versions"] = True
        if max_results is not None:
            kwargs["max_results"] = max_results
        blobs = client.bucket(bucket).list_blobs(**kwargs)
        results: list[StoredObject] = []
        for blob in blobs:
            results.append(
                StoredObject(
                    filename=blob.name.split("/")[-1],
                    storage_uri=f"gs://{bucket}/{blob.name}",
                    size_bytes=blob.size,
                    md5_hash=blob.md5_hash,
                    # Listing-time metadata callers may need (e.g. age-based
                    # cleanup, versioned wipe). Backend-specific, so kept in
                    # provider_details.
                    provider_details={
                        "time_created": blob.time_created,
                        "updated": blob.updated,
                        "generation": blob.generation,
                    },
                )
            )
        return results

    def _gcs_copy(self, source_uri: str, dest_uri: str, creds) -> None:
        src_bucket_name, src_key = self._parse_uri(source_uri)
        dst_bucket_name, dst_key = self._parse_uri(dest_uri)
        client = self._get_gcs_client(creds)
        src_bucket = client.bucket(src_bucket_name)
        dst_bucket = client.bucket(dst_bucket_name)
        src_blob = src_bucket.blob(src_key)
        dst_bucket.copy_blob(src_blob, dst_bucket, dst_key)

    def _gcs_move(self, source_uri: str, dest_uri: str, creds) -> None:
        # Fail-safe: copy, verify, then delete source (parity with the prior
        # GcsStorageService.move_file ordering).
        src_bucket_name, src_key = self._parse_uri(source_uri)
        dst_bucket_name, dst_key = self._parse_uri(dest_uri)
        client = self._get_gcs_client(creds)
        src_bucket = client.bucket(src_bucket_name)
        dst_bucket = client.bucket(dst_bucket_name)
        src_blob = src_bucket.blob(src_key)
        dst_bucket.copy_blob(src_blob, dst_bucket, dst_key)
        if not dst_bucket.blob(dst_key).exists():
            raise RuntimeError(f"Copy verification failed: {dest_uri} does not exist after copy")
        src_blob.delete()

    def _gcs_get_object_metadata(self, uri: str, creds) -> ObjectMetadata:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        if not blob.exists():
            raise StorageObjectNotFound(uri)
        blob.reload()
        return ObjectMetadata(
            uri=uri,
            size_bytes=blob.size,
            md5_hash=blob.md5_hash,
            content_type=blob.content_type,
            storage_class=blob.storage_class,
            updated=str(blob.updated) if blob.updated else None,
        )

    def _gcs_generate_signed_url(self, uri: str, method: str, expiry_seconds: int, content_type, creds) -> str:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        kwargs: dict = {"version": "v4", "expiration": expiry_seconds, "method": method}
        if content_type is not None:
            kwargs["content_type"] = content_type
        return blob.generate_signed_url(**kwargs)

    def _gcs_get_bucket_info(self, uri: str, creds) -> dict:
        bucket_name, _ = self._parse_uri(uri)
        bucket = self._get_gcs_client(creds).get_bucket(bucket_name)
        return {"versioning_enabled": bool(bucket.versioning_enabled)}

    def _gcs_create_resumable_upload_url(self, uri: str, content_type, size_bytes, origin, creds) -> str:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        return blob.create_resumable_upload_session(content_type=content_type, size=size_bytes, origin=origin)

    # -- Local mode implementations --

    async def _local_stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        os.makedirs(working_dir, exist_ok=True)
        staged_paths = []
        for record in file_records:
            filename = record.get("filename", f"file-{uuid.uuid4().hex[:8]}")
            src = record.get("local_path") or record.get("gcs_uri", "")
            dest = os.path.join(working_dir, filename)
            if src and os.path.exists(src):
                shutil.copy2(src, dest)
            else:
                # Create placeholder for local mode
                with open(dest, "w") as f:
                    f.write(f"# placeholder for {filename}\n")
            staged_paths.append(dest)
        return staged_paths

    async def _local_collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[dict]:
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        results_dir = f"{LOCAL_DATA_ROOT}/results/experiments/{experiment_id}/pipeline-runs/{run_id}"
        os.makedirs(results_dir, exist_ok=True)

        collected = []
        if os.path.isdir(working_dir):
            for fname in os.listdir(working_dir):
                src = os.path.join(working_dir, fname)
                if os.path.isfile(src):
                    dest = os.path.join(results_dir, fname)
                    shutil.copy2(src, dest)
                    collected.append(
                        {
                            "filename": fname,
                            "local_path": dest,
                            "gcs_uri": f"gs://{self.results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}/{fname}",
                            "size_bytes": os.path.getsize(dest),
                        }
                    )
        return collected

    def _local_storage_metrics(self) -> dict:
        from app.config import settings

        total_monthly = settings.local_storage_cost_monthly
        # Distribute proportionally across buckets (raw ~55%, working ~27%, results ~18%)
        raw_cost = round(total_monthly * 0.545, 4)
        working_cost = round(total_monthly * 0.273, 4)
        results_cost = round(total_monthly - raw_cost - working_cost, 4)
        return {
            "buckets": [
                {
                    "name": self.ingest_bucket,
                    "size_gb": 0.0,
                    "object_count": 0,
                    "storage_class": "STANDARD",
                    "cost_monthly_usd": 0.0,
                },
                {
                    "name": self.raw_bucket,
                    "size_gb": 2.5,
                    "object_count": 45,
                    "storage_class": "STANDARD",
                    "cost_monthly_usd": raw_cost,
                },
                {
                    "name": self.working_bucket,
                    "size_gb": 1.2,
                    "object_count": 120,
                    "storage_class": "STANDARD",
                    "cost_monthly_usd": working_cost,
                },
                {
                    "name": self.results_bucket,
                    "size_gb": 0.8,
                    "object_count": 35,
                    "storage_class": "STANDARD",
                    "cost_monthly_usd": results_cost,
                },
                {
                    "name": self.config_backups_bucket,
                    "size_gb": 0.01,
                    "object_count": 5,
                    "storage_class": "NEARLINE",
                    "cost_monthly_usd": 0.0,
                },
            ],
            "total_size_gb": 4.51,
            "total_cost_monthly_usd": total_monthly,
        }

    # -- GCS stage/collect helpers --

    def generate_stage_commands(self, file_records: list[dict], working_dir: str) -> list[str]:
        """Generate gsutil cp commands for an init container to stage input files."""
        commands = []
        for record in file_records:
            gcs_uri = record.get("gcs_uri", "")
            filename = record.get("filename", "unknown")
            dest = f"{working_dir}/{filename}"
            commands.append(f"gsutil cp {gcs_uri} {dest}")
        return commands

    def _get_gcs_client(self, credentials=None):
        """Get a Google Cloud Storage client. Tests mock this method.

        ``credentials`` (resolved off the event loop via ``_get_credentials``)
        are injected when present so signing and authorized ops work; ADC is the
        fallback for unauthenticated environments.
        """
        from google.cloud import storage

        return storage.Client(credentials=credentials) if credentials else storage.Client()

    # -- GCS API implementations (production) --

    async def _gcs_stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        """Generate stage commands for the init container (GCS mode).

        In GCS mode, staging is handled by the init container using gsutil,
        so we return the list of commands that the init container will execute.
        """
        return self.generate_stage_commands(file_records, working_dir)

    async def _gcs_collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[dict]:
        """List output objects in GCS and return file records."""
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")

        # Parse bucket and prefix from working_dir URI
        if working_dir.startswith("gs://"):
            parts = working_dir[5:].split("/", 1)
            bucket_name = parts[0]
            prefix = parts[1] if len(parts) > 1 else ""
        else:
            bucket_name = self.results_bucket
            prefix = f"experiments/{experiment_id}/pipeline-runs/{run_id}/"

        client = self._get_gcs_client()
        bucket = client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)

        collected = []
        for blob in blobs:
            # Extract filename from the full blob path
            filename = blob.name.split("/")[-1]
            if not filename:
                continue

            gcs_uri = f"gs://{bucket_name}/{blob.name}"
            collected.append(
                {
                    "filename": filename,
                    "gcs_uri": gcs_uri,
                    "size_bytes": blob.size,
                    "md5_hash": blob.md5_hash,
                    "experiment_id": experiment_id,
                    "pipeline_run_id": run_id,
                }
            )

        return collected

    async def _read_storage_config(self) -> dict[str, str]:
        """Read storage_deployed + the per-store bucket-name keys.

        Self-contained (Phase 3): this replaces the read that previously lived
        in GcsStorageService, so the adapter no longer imports the service.
        """
        from app.database import async_session_factory
        from app.platform.platform_config_service import PlatformConfigService

        keys = ["storage_deployed", *[f"{s.value}_bucket_name" for s in _METRICS_STORES]]
        async with async_session_factory() as session:
            return await PlatformConfigService.get_many(session, keys)

    async def _gcs_storage_metrics(self) -> dict:
        """Compute live per-bucket metrics directly via the GCS client.

        The adapter is now the single owner of GCS object/bucket operations;
        bucket enumeration that previously lived in GcsStorageService moved
        here, reversing the adapter -> service layering inversion. Blocking SDK
        calls run off the event loop.
        """
        config = await self._read_storage_config()
        if config.get("storage_deployed", "false") != "true":
            raise ValidationError("Storage infrastructure has not been deployed yet")

        creds = await self._get_credentials()
        raw = await asyncio.to_thread(self._gcs_collect_bucket_metrics, config, creds)

        buckets: list[dict[str, object]] = []
        total_gb = 0.0
        total_cost = 0.0
        for name, size_bytes, object_count, storage_class in raw:
            size_gb = size_bytes / (1024**3)
            cost = round(size_gb * 0.026, 2)
            total_gb += size_gb
            total_cost += cost
            buckets.append(
                {
                    "name": name,
                    "size_gb": round(size_gb, 2),
                    "object_count": object_count,
                    "storage_class": storage_class,
                    "cost_monthly_usd": cost,
                }
            )

        return {
            "buckets": buckets,
            "total_size_gb": round(total_gb, 2),
            "total_cost_monthly_usd": round(total_cost, 2),
        }

    def _gcs_collect_bucket_metrics(self, config: dict, creds) -> list[tuple]:
        """Enumerate configured buckets and return (name, size_bytes, count, class)."""
        client = self._get_gcs_client(creds)
        out: list[tuple] = []
        for store in _METRICS_STORES:
            bucket_name = config.get(f"{store.value}_bucket_name", "")
            if not bucket_name or bucket_name == "null":
                continue
            bucket = client.get_bucket(bucket_name)
            blobs = list(client.list_blobs(bucket_name))
            total_size = sum(b.size or 0 for b in blobs)
            storage_class = bucket.storage_class or "STANDARD"
            out.append((bucket_name, total_size, len(blobs), storage_class))
        return out

    async def get_bucket_admin_metrics(self, bucket_name: str) -> BucketAdminMetrics:
        """Rich per-bucket admin metrics (size/lifecycle/versioning/created).

        Owns the bucket-level google-cloud-storage enumeration that previously
        lived in GcsStorageService.get_bucket_metrics (Phase 9 / Stage 3b). The
        blocking SDK walk runs off the event loop.
        """
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_bucket_admin_metrics, bucket_name, creds)

    async def delete_bucket(self, bucket_name: str) -> None:
        """Delete a bucket and all its contents (force=True). DESTRUCTIVE.

        Owns the whole-bucket delete that previously lived in
        OrphanedResourceService._cleanup_gcs_bucket (Phase 9 / Stage 3b.5).
        """
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_delete_bucket, bucket_name, creds)

    def _gcs_delete_bucket(self, bucket_name: str, creds) -> None:
        client = self._get_gcs_client(creds)
        client.bucket(bucket_name).delete(force=True)

    def native_upload_client(self, credentials=None):
        """Raw synchronous google-cloud-storage client (transitional escape hatch).

        Mirrors the prior ``storage.Client(credentials=...)`` construction the
        reference-data upload helpers + the half-built importer used inline, so
        the SDK import now lives here instead of the service layer.
        """
        return self._get_gcs_client(credentials)

    def _gcs_bucket_admin_metrics(self, bucket_name: str, creds) -> BucketAdminMetrics:
        client = self._get_gcs_client(creds)
        bucket = client.get_bucket(bucket_name)
        blobs = list(client.list_blobs(bucket_name))
        total_size = sum(b.size or 0 for b in blobs)
        return BucketAdminMetrics(
            size_bytes=total_size,
            object_count=len(blobs),
            storage_class=bucket.storage_class or "STANDARD",
            versioning_enabled=bool(bucket.versioning_enabled),
            lifecycle_summaries=self._summarize_lifecycle(bucket.lifecycle_rules or []),
            created_at=str(bucket.time_created) if bucket.time_created else None,
        )

    @staticmethod
    def _summarize_lifecycle(rules) -> list[str]:
        """Render GCS lifecycle rules into cloud-agnostic human summaries.

        The GCS-specific rule shape (action.type / condition.age) is parsed here
        so the service layer consumes neutral strings only.
        """
        summaries: list[str] = []
        for rule in rules:
            action = rule.get("action", {})
            condition = rule.get("condition", {})
            action_type = action.get("type", "")
            if action_type == "SetStorageClass":
                target = action.get("storageClass", "")
                age = condition.get("age", "?")
                summaries.append(f"Transition to {target} after {age} days")
            elif action_type == "Delete":
                age = condition.get("age", "?")
                summaries.append(f"Delete after {age} days")
        return summaries

    async def list_lifecycle_policies(self, prefix: str) -> list[dict]:
        """Project-level lifecycle enumeration for buckets matching ``prefix``.

        Owns the google-cloud-storage walk that previously lived in
        StorageService.get_lifecycle_policies (Phase 9 / Stage 3b). Off the loop.
        """
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_list_lifecycle_policies, prefix, creds)

    def _gcs_list_lifecycle_policies(self, prefix: str, creds) -> list[dict]:
        client = self._get_gcs_client(creds)
        policies: list[dict] = []
        for bucket in client.list_buckets(prefix=prefix):
            rules = [dict(rule) for rule in (bucket.lifecycle_rules or [])]
            policies.append(
                {
                    "bucket_name": bucket.name,
                    "rules": rules,
                    "enabled": len(rules) > 0,
                }
            )
        return policies

    async def query_bucket_stats(self, prefix: str) -> list[dict]:
        """Project-level per-bucket usage for buckets matching ``prefix``.

        Owns the google-cloud-storage walk that previously lived in
        StorageService._query_gcs_buckets (Phase 9 / Stage 3b). Off the loop.
        """
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._gcs_query_bucket_stats, prefix, creds)

    def _gcs_query_bucket_stats(self, prefix: str, creds) -> list[dict]:
        client = self._get_gcs_client(creds)
        results: list[dict] = []
        for bucket in client.list_buckets(prefix=prefix):
            total_bytes = 0
            object_count = 0
            by_class: dict[str, int] = {}
            for blob in bucket.list_blobs():
                total_bytes += blob.size or 0
                object_count += 1
                sc = blob.storage_class or "STANDARD"
                by_class[sc] = by_class.get(sc, 0) + (blob.size or 0)
            results.append(
                {
                    "name": bucket.name,
                    "total_bytes": total_bytes,
                    "object_count": object_count,
                    "by_storage_class": by_class,
                }
            )
        return results
