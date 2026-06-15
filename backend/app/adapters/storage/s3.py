"""S3 storage adapter (Stage 6a: S3StorageProvider).

The AWS column of the StorageProvider seam. Tier-A storage (pipeline staging) and
Tier-B object store (read/write/upload/download/list/copy/move/metadata/signed
URLs) in one class, for ``s3://`` URIs, mirroring ``GcsStorageProvider``.

Like the GCS adapter it supports a local/mock mode for development (object ops
emulate an object store on the local filesystem, so dev/CI need no real S3) and a
real S3 mode via the boto3 client. Mode is the shared ``BIOAF_COMPUTE_MODE``
environment variable. boto3 (and any ``s3://`` literal / ``aws`` CLI string) lives
here inside ``adapters/`` by design: the BAL guard forbids them everywhere else.

This module is built in sub-blocks (6a.1 foundation: URI/scheme + CLI staging +
capabilities; 6a.2+ object-store ops, signed URLs, pipeline staging, bucket admin).
Methods not yet implemented inherit the base ``NotImplementedError`` so an
incomplete S3 provider is unreachable on a GCP install (POLICY never resolves
``s3`` there) and cannot regress the live product.
"""

import asyncio
import logging
import os
import shutil
from typing import BinaryIO
from urllib.parse import urlparse

from app.adapters.base import StorageProvider
from app.adapters.capabilities import ProviderCapabilities
from app.exceptions import ValidationError
from app.adapters.models import (
    ObjectMetadata,
    StorageObjectNotFound,
    StorageStore,
    StoredObject,
)

logger = logging.getLogger("bioaf.adapters.storage.s3")

# Local mode emulates the object store on the filesystem, exactly like the GCS
# adapter (``s3://<bucket>/<key>`` maps to LOCAL_DATA_ROOT/_objects/<bucket>/<key>),
# so the same LOCAL_DATA_ROOT layout serves both backends in dev/CI.
LOCAL_DATA_ROOT = os.environ.get("BIOAF_LOCAL_DATA_ROOT", "/tmp/bioaf-data")
_LOCAL_OBJECTS_DIR = "_objects"


# -- Local-mode filesystem helpers (the s3:// emulation; parity with gcs.py) ---


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


def _is_not_found(error) -> bool:
    """True if a botocore ClientError is an object-absent error.

    Normalizes S3's several not-found shapes: ``NoSuchKey`` (get_object),
    ``404``/``NotFound`` (head_object), so callers map any of them onto
    ``StorageObjectNotFound`` without importing botocore.
    """
    err = getattr(error, "response", {}) or {}
    code = err.get("Error", {}).get("Code", "")
    status = err.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"NoSuchKey", "NotFound", "404"} or status == 404


def _etag_to_md5(etag: str | None) -> str | None:
    """An S3 ETag is the object's MD5 only for a single-part upload; a multipart
    ETag is ``<hash>-<partcount>`` and is not a usable checksum. Return the hex MD5
    when single-part, else None (the raw ETag is kept in provider_details)."""
    if not etag:
        return None
    etag = etag.strip('"')
    return None if "-" in etag else etag


# Map the neutral signed-URL ``method`` onto the boto3 client operation that
# generate_presigned_url signs for.
_S3_PRESIGN_METHODS = {
    "GET": "get_object",
    "PUT": "put_object",
    "DELETE": "delete_object",
    "HEAD": "head_object",
}


class S3StorageProvider(StorageProvider):
    """S3 storage backend with local mode for development."""

    def __init__(self, org_slug: str = "demo"):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._org_slug = org_slug
        # Region for the boto3 client (real mode). Resolved from the standard AWS
        # env vars first; the explicit per-install region (platform_config) is read
        # alongside bucket config in the object-store sub-blocks.
        self._region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        # Lazily-loaded, cached for the adapter's lifetime, mirroring the GCS adapter.
        self._credentials = None
        self._credentials_loaded = False
        self._bucket_config: dict[str, str] | None = None

    def capabilities(self) -> ProviderCapabilities:
        """S3 supports signed-URL direct upload and per-tier storage metrics."""
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
        return f"/data/inputs/{file_record.get('filename', 'unknown')}"

    async def resolve_output_path(self, pipeline_run: dict, filename: str) -> str:
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        if self.is_local:
            return f"{LOCAL_DATA_ROOT}/results/experiments/{experiment_id}/pipeline-runs/{run_id}/{filename}"
        return f"s3://{self.results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}/{filename}"

    async def stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        # Pipeline staging (local + real-S3) lands in sub-block 6a.5.
        raise NotImplementedError("S3 stage_inputs is implemented in Stage 6a.5")

    async def collect_outputs(self, working_dir: str, pipeline_run: dict):
        # Pipeline staging (local + real-S3) lands in sub-block 6a.5.
        raise NotImplementedError("S3 collect_outputs is implemented in Stage 6a.5")

    async def get_storage_metrics(self):
        # Storage metrics (local + real-S3) lands in sub-block 6a.5.
        raise NotImplementedError("S3 get_storage_metrics is implemented in Stage 6a.5")

    # -- URI / scheme (the s3:// counterpart of the GCS gs:// methods) ---------

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse ``s3://bucket/key`` into (bucket, key)."""
        if not uri.startswith("s3://"):
            raise ValidationError(f"Not a storage URI: {uri!r}")
        parsed = urlparse(uri)
        return parsed.netloc, parsed.path.lstrip("/")

    def _local_bucket(self, store: StorageStore) -> str:
        return f"bioaf-{store.value}-{self._org_slug}"

    def _local_path(self, uri: str) -> str:
        bucket, key = self._parse_uri(uri)
        # The key can derive from user input, and local mode maps it onto a real
        # filesystem path. Reject traversal in the key (the explicit '..'/abs check
        # is a CodeQL-recognised path-injection barrier), then confirm the resolved
        # real path stays under the local object root.
        if os.path.isabs(key) or ".." in key.split("/"):
            raise ValidationError(f"Storage key escapes the local object root: {key!r}")
        base = os.path.realpath(os.path.join(LOCAL_DATA_ROOT, _LOCAL_OBJECTS_DIR))
        full = os.path.realpath(os.path.join(base, bucket, key))
        if full != base and not full.startswith(base + os.sep):
            raise ValidationError(f"Storage path escapes the local object root: {uri!r}")
        return full

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
            return f"s3://{self._local_bucket(store)}/{key}"
        config = await self._get_bucket_config()
        bucket = config.get(f"{store.value}_bucket_name")
        if not bucket or bucket == "null":
            raise ValidationError(f"No bucket configured for store {store.value!r}")
        return f"s3://{bucket}/{key}"

    def build_uri(self, bucket: str, key: str) -> str:
        return f"s3://{bucket}/{key.lstrip('/')}"

    def parse_uri(self, uri: str) -> tuple[str, str]:
        return self._parse_uri(uri)

    # -- Container-side CLI staging (the aws-CLI counterpart of gsutil) --------

    def cli_auth_command(self, key_file: str) -> str:
        # S3 authenticates ambiently from the pod's instance profile / IRSA role,
        # so there is no explicit CLI auth step (mirrors the NFS no-auth case).
        return ""

    def cli_copy_in(self, uri: str, local_path: str) -> str:
        return f"aws s3 cp {uri} {local_path}"

    def cli_copy_out(self, local_path: str, uri: str) -> str:
        return f"aws s3 cp --recursive {local_path} {uri}"

    def sync_in_command(self, remote_prefix: str, local_dir: str) -> list[str]:
        # `|| true` so a missing/empty prefix does not fail the stage-in init
        # container, matching the GCS adapter's tolerant rsync.
        return ["/bin/sh", "-c", f"aws s3 sync {remote_prefix} {local_dir} || true"]

    def sync_out_command(self, local_dir: str, remote_prefix: str) -> list[str]:
        return ["/bin/sh", "-c", f"aws s3 sync {local_dir} {remote_prefix}"]

    def staging_image(self) -> str:
        # amazon/aws-cli ships the `aws s3` CLI for stage in/out.
        return "amazon/aws-cli"

    def image_storage_pip_packages(self) -> str:
        # Baked into built pipeline/notebook images so user code can read/write S3
        # (boto3) and shell out to the CLI (awscli). Bounded to the 1.x majors.
        return "boto3>=1.43,<2 awscli>=1.40,<2"

    # -- Credentials + client factory (real S3 mode) --------------------------

    async def _get_credentials(self):
        """Resolve and cache AWS credentials for the boto3 client.

        Stage 6c (the AWS Credentials provider: STS / assume-role / instance
        profile) will supply explicit credentials here, mirroring how the GCS
        adapter resolves impersonation / SA-key creds. Until 6c lands this returns
        ``None`` so boto3 uses its ambient credential chain (env vars, shared
        config, the instance-profile / IRSA role on a real EC2/EKS host), which is
        the standard AWS pattern.
        """
        if not self._credentials_loaded:
            self._credentials = None
            self._credentials_loaded = True
        return self._credentials

    def _get_s3_client(self, credentials=None):
        """Construct the boto3 S3 client. boto3 is imported lazily and only here,
        so the SDK stays inside ``adapters/`` and local mode never imports it."""
        import boto3

        kwargs: dict = {}
        if self._region:
            kwargs["region_name"] = self._region
        if credentials:
            # 6c hands back an explicit-credentials kwargs dict (access key / secret
            # / session token); the ambient chain (None) is the default until then.
            kwargs.update(credentials)
        return boto3.client("s3", **kwargs)

    # -- Object-store interface (Phase 3): core read/write/delete (6a.2) -------
    #
    # URI-first: every op takes an ``s3://bucket/key`` URI. In local mode the URI
    # maps onto LOCAL_DATA_ROOT/_objects/<bucket>/<key>; in S3 mode it goes through
    # the boto3 client off the event loop via ``asyncio.to_thread`` (parity with
    # the GCS adapter's ``_gcs_*`` helpers).

    async def read_text(self, uri: str, *, encoding: str = "utf-8") -> str:
        return (await self.read_bytes(uri)).decode(encoding)

    async def read_bytes(self, uri: str) -> bytes:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            return await asyncio.to_thread(_read_file_bytes, path)
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_read_bytes, uri, creds)

    async def write_text(self, uri: str, text: str, *, content_type: str = "text/plain") -> None:
        await self.write_bytes(uri, text.encode("utf-8"), content_type=content_type)

    async def write_bytes(self, uri: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        if self.is_local:
            await asyncio.to_thread(_write_file_bytes, self._local_path(uri), data)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_write_bytes, uri, data, content_type, creds)

    async def upload_file(self, uri: str, file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        if self.is_local:
            await asyncio.to_thread(_write_file_stream, self._local_path(uri), file_obj)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_upload_file, uri, file_obj, content_type, creds)

    async def upload_filename(self, uri: str, local_path: str, *, content_type: str | None = None) -> None:
        if self.is_local:
            await asyncio.to_thread(_copy_file, local_path, self._local_path(uri))
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_upload_filename, uri, local_path, content_type, creds)

    async def download_to_file(self, uri: str, file_obj: BinaryIO) -> None:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            await asyncio.to_thread(_stream_file_into, path, file_obj)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_download_to_file, uri, file_obj, creds)

    async def download_to_filename(self, uri: str, local_path: str) -> None:
        if self.is_local:
            src = self._local_path(uri)
            if not os.path.exists(src):
                raise StorageObjectNotFound(uri)
            await asyncio.to_thread(_copy_file, src, local_path)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_download_to_filename, uri, local_path, creds)

    async def delete(self, uri: str, *, generation: int | None = None) -> None:
        if self.is_local:
            path = self._local_path(uri)
            if os.path.exists(path):
                await asyncio.to_thread(os.remove, path)
            return
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_delete, uri, generation, creds)

    async def exists(self, uri: str) -> bool:
        if self.is_local:
            return os.path.exists(self._local_path(uri))
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_exists, uri, creds)

    # -- Object-store interface (Phase 3): list/copy/move/metadata (6a.3) ------

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
            self._s3_list_objects, uri_prefix, recursive, include_versions, max_results, creds
        )

    async def copy(self, source_uri: str, dest_uri: str) -> str:
        if self.is_local:
            await asyncio.to_thread(self._local_copy, source_uri, dest_uri)
            return dest_uri
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_copy, source_uri, dest_uri, creds)
        return dest_uri

    async def move(self, source_uri: str, dest_uri: str) -> str:
        if self.is_local:
            await asyncio.to_thread(self._local_move, source_uri, dest_uri)
            return dest_uri
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_move, source_uri, dest_uri, creds)
        return dest_uri

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            size = await asyncio.to_thread(os.path.getsize, path)
            return ObjectMetadata(uri=uri, size_bytes=size)
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_get_object_metadata, uri, creds)

    async def get_bucket_info(self, uri: str) -> dict:
        if self.is_local:
            # The local filesystem store has no object versioning.
            return {"versioning_enabled": False}
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_get_bucket_info, uri, creds)

    # -- Object-store interface (Phase 3): signed URLs + resumable (6a.4) ------
    #
    # No is_local branch (parity with the GCS adapter): signed URLs are a real-
    # cloud feature, so local/dev mode does not mint them.

    async def generate_signed_url(
        self,
        uri: str,
        *,
        method: str = "GET",
        expiry_seconds: int = 3600,
        content_type: str | None = None,
    ) -> str:
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_generate_signed_url, uri, method, expiry_seconds, content_type, creds)

    async def create_resumable_upload_url(
        self,
        uri: str,
        *,
        content_type: str = "application/octet-stream",
        size_bytes: int | None = None,
        origin: str | None = None,
    ) -> str:
        # S3 has no direct analog to a GCS resumable upload session. A presigned PUT
        # satisfies the single-URL seam contract for client-direct uploads up to the
        # 5 GiB single-PUT limit. True >5 GiB resumable parity needs the seam
        # generalized to a multipart protocol (initiate -> presigned part URLs ->
        # complete); tracked as a residual and validated against the live account.
        # ``size_bytes``/``origin`` are GCS-session knobs with no presigned-PUT
        # analog (S3 cross-origin uploads rely on bucket CORS, a 7a substrate item),
        # so they are accepted and ignored here.
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_create_presigned_put, uri, content_type, creds)

    # -- Local-mode object-store helpers (s3:// emulation) --------------------

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
                        storage_uri=f"s3://{bucket}/{rel}",
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

    # -- boto3-backed helpers (run in a thread; real S3 only) -----------------

    def _s3_read_bytes(self, uri: str, creds) -> bytes:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        try:
            resp = self._get_s3_client(creds).get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if _is_not_found(e):
                raise StorageObjectNotFound(uri) from e
            raise
        return resp["Body"].read()

    def _s3_write_bytes(self, uri: str, data: bytes, content_type: str, creds) -> None:
        bucket, key = self._parse_uri(uri)
        self._get_s3_client(creds).put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)

    def _s3_upload_file(self, uri: str, file_obj, content_type, creds) -> None:
        bucket, key = self._parse_uri(uri)
        if hasattr(file_obj, "seek"):
            try:
                file_obj.seek(0)
            except (OSError, ValueError):
                pass
        extra = {"ContentType": content_type} if content_type else None
        self._get_s3_client(creds).upload_fileobj(file_obj, bucket, key, ExtraArgs=extra)

    def _s3_upload_filename(self, uri: str, local_path: str, content_type, creds) -> None:
        bucket, key = self._parse_uri(uri)
        extra = {"ContentType": content_type} if content_type else None
        self._get_s3_client(creds).upload_file(local_path, bucket, key, ExtraArgs=extra)

    def _s3_download_to_file(self, uri: str, file_obj, creds) -> None:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        try:
            self._get_s3_client(creds).download_fileobj(bucket, key, file_obj)
        except ClientError as e:
            if _is_not_found(e):
                raise StorageObjectNotFound(uri) from e
            raise

    def _s3_download_to_filename(self, uri: str, local_path: str, creds) -> None:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            self._get_s3_client(creds).download_file(bucket, key, local_path)
        except ClientError as e:
            if _is_not_found(e):
                raise StorageObjectNotFound(uri) from e
            raise

    def _s3_delete(self, uri: str, generation, creds) -> None:
        bucket, key = self._parse_uri(uri)
        if generation is not None:
            # GCS targets a specific object version by int ``generation``; S3 uses a
            # string ``VersionId``. Bridging the two (for noncurrent-version wipes in
            # orphaned-resource cleanup) needs the seam generalized to an opaque
            # version token, deferred with the AWS orphan-cleanup work (6b).
            raise NotImplementedError(
                "S3 version-targeted delete is deferred to AWS orphaned-resource "
                "cleanup; S3 uses a string VersionId, not an int generation"
            )
        # delete_object is idempotent: deleting a missing key is not an error.
        self._get_s3_client(creds).delete_object(Bucket=bucket, Key=key)

    def _s3_exists(self, uri: str, creds) -> bool:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        try:
            self._get_s3_client(creds).head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if _is_not_found(e):
                return False
            raise

    @staticmethod
    def _stored_object_from_listing(bucket: str, obj: dict, *, version: bool) -> StoredObject:
        key = obj["Key"]
        details: dict = {
            "last_modified": str(obj["LastModified"]) if obj.get("LastModified") else None,
            "etag": obj.get("ETag"),
            "storage_class": obj.get("StorageClass"),
        }
        if version:
            details["version_id"] = obj.get("VersionId")
            details["is_latest"] = obj.get("IsLatest")
        return StoredObject(
            filename=key.split("/")[-1],
            storage_uri=f"s3://{bucket}/{key}",
            size_bytes=obj.get("Size"),
            md5_hash=_etag_to_md5(obj.get("ETag")),
            provider_details=details,
        )

    def _s3_list_objects(
        self, uri_prefix: str, recursive: bool, include_versions: bool, max_results: int | None, creds
    ) -> list[StoredObject]:
        bucket, key_prefix = self._parse_uri(uri_prefix)
        client = self._get_s3_client(creds)
        page_kwargs: dict = {"Bucket": bucket, "Prefix": key_prefix}
        if not recursive:
            page_kwargs["Delimiter"] = "/"
        op = "list_object_versions" if include_versions else "list_objects_v2"
        listing_key = "Versions" if include_versions else "Contents"
        results: list[StoredObject] = []
        for page in client.get_paginator(op).paginate(**page_kwargs):
            for obj in page.get(listing_key, []):
                results.append(self._stored_object_from_listing(bucket, obj, version=include_versions))
                if max_results is not None and len(results) >= max_results:
                    return results
        return results

    def _s3_copy(self, source_uri: str, dest_uri: str, creds) -> None:
        src_bucket, src_key = self._parse_uri(source_uri)
        dst_bucket, dst_key = self._parse_uri(dest_uri)
        # client.copy() (managed) auto-uses multipart for objects >5 GiB (genomics-scale).
        self._get_s3_client(creds).copy({"Bucket": src_bucket, "Key": src_key}, dst_bucket, dst_key)

    def _s3_move(self, source_uri: str, dest_uri: str, creds) -> None:
        from botocore.exceptions import ClientError

        src_bucket, src_key = self._parse_uri(source_uri)
        dst_bucket, dst_key = self._parse_uri(dest_uri)
        client = self._get_s3_client(creds)
        # Fail-safe: copy, verify the destination exists, then delete the source
        # (parity with the GCS adapter's move ordering: never lose the source on a
        # failed/unverified copy).
        client.copy({"Bucket": src_bucket, "Key": src_key}, dst_bucket, dst_key)
        try:
            client.head_object(Bucket=dst_bucket, Key=dst_key)
        except ClientError as e:
            raise RuntimeError(f"Copy verification failed: {dest_uri} does not exist after copy") from e
        client.delete_object(Bucket=src_bucket, Key=src_key)

    def _s3_get_object_metadata(self, uri: str, creds) -> ObjectMetadata:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        try:
            resp = self._get_s3_client(creds).head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if _is_not_found(e):
                raise StorageObjectNotFound(uri) from e
            raise
        return ObjectMetadata(
            uri=uri,
            size_bytes=resp.get("ContentLength"),
            md5_hash=_etag_to_md5(resp.get("ETag")),
            content_type=resp.get("ContentType"),
            storage_class=resp.get("StorageClass", "STANDARD"),
            updated=str(resp["LastModified"]) if resp.get("LastModified") else None,
        )

    def _s3_get_bucket_info(self, uri: str, creds) -> dict:
        bucket_name, _ = self._parse_uri(uri)
        resp = self._get_s3_client(creds).get_bucket_versioning(Bucket=bucket_name)
        return {"versioning_enabled": resp.get("Status") == "Enabled"}

    def _s3_generate_signed_url(self, uri: str, method: str, expiry_seconds: int, content_type, creds) -> str:
        bucket, key = self._parse_uri(uri)
        client_method = _S3_PRESIGN_METHODS.get(method.upper())
        if client_method is None:
            raise ValidationError(f"Unsupported signed-URL method: {method!r}")
        params: dict = {"Bucket": bucket, "Key": key}
        if content_type is not None:
            params["ContentType"] = content_type
        return self._get_s3_client(creds).generate_presigned_url(client_method, Params=params, ExpiresIn=expiry_seconds)

    def _s3_create_presigned_put(self, uri: str, content_type: str, creds) -> str:
        bucket, key = self._parse_uri(uri)
        return self._get_s3_client(creds).generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=3600,
        )
