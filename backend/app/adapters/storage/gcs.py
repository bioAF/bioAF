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
from app.adapters.models import (
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
                    k: v
                    for k, v in d.items()
                    if k not in {"filename", "gcs_uri", "size_bytes", "md5_hash"}
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
            raise ValueError(f"Not a storage URI: {uri!r}")
        parsed = urlparse(uri)
        return parsed.netloc, parsed.path.lstrip("/")

    def _local_bucket(self, store: StorageStore) -> str:
        return f"bioaf-{store.value}-{self._org_slug}"

    def _local_path(self, uri: str) -> str:
        bucket, key = self._parse_uri(uri)
        return os.path.join(LOCAL_DATA_ROOT, _LOCAL_OBJECTS_DIR, bucket, key)

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
        from app.platform import credential_injector
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
            raise ValueError(f"No bucket configured for store {store.value!r}")
        return f"gs://{bucket}/{key}"

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

    async def write_bytes(
        self, uri: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
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

    async def delete(self, uri: str) -> None:
        if self.is_local:
            path = self._local_path(uri)
            if os.path.exists(path):
                await asyncio.to_thread(os.remove, path)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._gcs_delete, uri, creds)

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

    async def generate_signed_url(
        self,
        uri: str,
        *,
        method: str = "GET",
        expiry_seconds: int = 3600,
        content_type: str | None = None,
    ) -> str:
        creds = await self._get_credentials()
        return await asyncio.to_thread(
            self._gcs_generate_signed_url, uri, method, expiry_seconds, content_type, creds
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

    def _gcs_delete(self, uri: str, creds) -> None:
        from google.api_core.exceptions import NotFound

        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
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

    def _gcs_generate_signed_url(
        self, uri: str, method: str, expiry_seconds: int, content_type, creds
    ) -> str:
        bucket, key = self._parse_uri(uri)
        blob = self._get_gcs_client(creds).bucket(bucket).blob(key)
        kwargs: dict = {"version": "v4", "expiration": expiry_seconds, "method": method}
        if content_type is not None:
            kwargs["content_type"] = content_type
        return blob.generate_signed_url(**kwargs)

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
            raise ValueError("Storage infrastructure has not been deployed yet")

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
