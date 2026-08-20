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


def _read_file_prefix(path: str, length: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(length)


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

# The logical stores enumerated for storage metrics (parity with the GCS adapter's
# _METRICS_STORES: BACKUPS is not a managed-metrics bucket).
_METRICS_STORES = (
    StorageStore.INGEST,
    StorageStore.RAW,
    StorageStore.WORKING,
    StorageStore.RESULTS,
    StorageStore.REFERENCES,
    StorageStore.LITERATURE,
    StorageStore.CONFIG_BACKUPS,
)

# Rough S3 Standard $/GiB-month for the dashboard cost estimate. Cost is an
# ALLOWED-DIVERGENCE surface (provider-reported), so this differs from the GCS
# adapter's ~0.026 by design.
_S3_STANDARD_COST_PER_GB = 0.023


def _summarize_s3_lifecycle(rules) -> list[str]:
    """Render S3 lifecycle rules into the same cloud-agnostic human summaries the
    GCS adapter emits, so the service layer consumes neutral strings on both clouds.
    The S3 rule shape (Transitions[].Days/StorageClass, Expiration.Days) is parsed
    here. Disabled rules are skipped."""
    summaries: list[str] = []
    for rule in rules:
        if rule.get("Status") != "Enabled":
            continue
        for transition in rule.get("Transitions", []):
            days = transition.get("Days", "?")
            summaries.append(f"Transition to {transition.get('StorageClass', '')} after {days} days")
        expiration = rule.get("Expiration", {})
        if "Days" in expiration:
            summaries.append(f"Delete after {expiration['Days']} days")
    return summaries


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
        if self.is_local:
            return await self._local_stage_inputs(file_records, working_dir)
        # Real S3: staging runs in an init container via the CLI, so (like the GCS
        # adapter) return the command strings the init container executes.
        return self.generate_stage_commands(file_records, working_dir)

    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[StoredObject]:
        if self.is_local:
            items = await self._local_collect_outputs(working_dir, pipeline_run)
            return [
                StoredObject(filename=d["filename"], storage_uri=d["storage_uri"], size_bytes=d.get("size_bytes"))
                for d in items
            ]
        run_id = pipeline_run.get("id", "unknown")
        experiment_id = pipeline_run.get("experiment_id", "unknown")
        if working_dir.startswith("s3://"):
            prefix_uri = working_dir if working_dir.endswith("/") else working_dir + "/"
        else:
            prefix_uri = f"s3://{self.results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}/"
        objs = await self.list_objects(prefix_uri)
        return [o for o in objs if o.filename]

    async def get_storage_metrics(self) -> StorageMetrics:
        d = self._local_storage_metrics() if self.is_local else await self._s3_storage_metrics()
        return StorageMetrics(
            buckets=[BucketMetrics(**b) for b in d.get("buckets", [])],
            total_size_gb=d.get("total_size_gb", 0.0),
            total_cost_monthly_usd=d.get("total_cost_monthly_usd", 0.0),
        )

    def generate_stage_commands(self, file_records: list[dict], working_dir: str) -> list[str]:
        """Generate ``aws s3 cp`` commands for an init container to stage inputs.

        The S3 counterpart of the GCS adapter's gsutil staging commands; reuses the
        ``cli_copy_in`` seam token so the CLI string lives in exactly one place.
        Reads ``storage_uri`` (the neutral field), falling back to the retained
        ``gcs_uri`` mirror for records that predate the rename.
        """
        commands = []
        for record in file_records:
            uri = record.get("storage_uri") or record.get("gcs_uri", "")
            filename = record.get("filename", "unknown")
            commands.append(self.cli_copy_in(uri, f"{working_dir}/{filename}"))
        return commands

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

    def cli_copy_out_file(self, local_path: str, uri: str) -> str:
        return f"aws s3 cp {local_path} {uri}"

    def sync_in_command(self, remote_prefix: str, local_dir: str) -> list[str]:
        # `|| true` so a missing/empty prefix does not fail the stage-in init
        # container, matching the GCS adapter's tolerant rsync.
        return ["/bin/sh", "-c", f"aws s3 sync {remote_prefix} {local_dir} || true"]

    def sync_out_command(self, local_dir: str, remote_prefix: str) -> list[str]:
        return ["/bin/sh", "-c", f"aws s3 sync {local_dir} {remote_prefix}"]

    def staging_image(self) -> str:
        # amazon/aws-cli ships the `aws s3` CLI for stage in/out.
        return "amazon/aws-cli"

    def nextflow_scratch_directives(self, work_dir: str) -> list[str]:
        # The S3 analog of the GCS ScratchWorkDir: Wave + Fusion mount the s3://
        # workDir as a local POSIX filesystem inside each task pod (Fusion supports
        # S3 natively), so head and process pods exchange .command.run scripts over
        # the object store without a shared RWX PVC. Pods authenticate to S3 via
        # IRSA, and fusion.exportStorageCredentials forwards those creds to Fusion.
        return [
            f"workDir = '{work_dir}'",
            "wave.enabled = true",
            "fusion.enabled = true",
            "fusion.exportStorageCredentials = true",
        ]

    def image_storage_pip_packages(self) -> str:
        # Baked into built pipeline/notebook images so user code can read/write S3
        # (boto3) and shell out to the CLI (awscli). Bounded to the 1.x majors.
        # Each spec is single-quoted because this string is substituted into a
        # Dockerfile `RUN pip install ...` shell line: an UNQUOTED upper bound like
        # `<2` is parsed by the shell as an input redirection ("2: No such file or
        # directory"), failing the image build. (GCS uses `==` pins, which have no
        # shell-metacharacters, so it needs no quoting.)
        return "'boto3>=1.43,<2' 'awscli>=1.40,<2'"

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
            # Resolve the boto3 client region from platform_config when the AWS env
            # vars did not supply one. Object ops -- especially presigned-URL
            # signing, which embeds the region in the SigV4 signature -- must
            # target the install's actual region; otherwise boto3 defaults to
            # us-east-1 and a presigned URL for a non-us-east-1 bucket is rejected.
            # Best-effort: if the config is unreachable (tests / pre-DB), fall back
            # to boto3's own region resolution.
            if not self._region and not self.is_local:
                try:
                    from app.database import async_session_factory
                    from app.platform.platform_config_service import PlatformConfigService

                    async with async_session_factory() as session:
                        cfg = await PlatformConfigService.get_many(session, ["aws_region"])
                    self._region = cfg.get("aws_region") or None
                except Exception:  # pragma: no cover - defensive fallback
                    pass
            self._credentials_loaded = True
        return self._credentials

    def _get_s3_client(self, credentials=None):
        """Construct the boto3 S3 client. boto3 is imported lazily and only here,
        so the SDK stays inside ``adapters/`` and local mode never imports it."""
        import boto3
        from botocore.config import Config

        # Virtual-hosted addressing + SigV4: without addressing_style="virtual",
        # boto3 emits presigned URLs against the GLOBAL host (bucket.s3.amazonaws.com,
        # which routes to us-east-1), so a presigned PUT/GET for a bucket in another
        # region is answered with a 307 redirect that browsers do not follow on an
        # upload -- breaking client-direct file upload/download. "virtual" pins the
        # presigned host to the bucket's regional endpoint (bucket.s3.<region>.amazonaws.com).
        kwargs: dict = {"config": Config(signature_version="s3v4", s3={"addressing_style": "virtual"})}
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

    async def read_prefix(self, uri: str, length: int) -> bytes:
        if self.is_local:
            path = self._local_path(uri)
            if not os.path.exists(path):
                raise StorageObjectNotFound(uri)
            return await asyncio.to_thread(_read_file_prefix, path, length)
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_read_prefix, uri, length, creds)

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

    # -- Bucket-admin enumeration (Tier-2): real S3 only, no local mode -------

    async def get_bucket_admin_metrics(self, bucket_name: str) -> BucketAdminMetrics:
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_bucket_admin_metrics, bucket_name, creds)

    async def delete_bucket(self, bucket_name: str) -> None:
        """Delete a bucket and ALL its contents. DESTRUCTIVE and irreversible.

        S3 DeleteBucket requires an empty bucket, so this purges every object
        (and all noncurrent versions + delete markers) first, then deletes the
        bucket. Used by orphaned-resource cleanup.
        """
        creds = await self._get_credentials()
        await asyncio.to_thread(self._s3_delete_bucket, bucket_name, creds)

    def native_upload_client(self, credentials=None):
        """Raw synchronous boto3 S3 client (transitional escape hatch), mirroring the
        GCS adapter's native_upload_client for the reference-data upload helpers."""
        return self._get_s3_client(credentials)

    async def list_lifecycle_policies(self, prefix: str) -> list[dict]:
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_list_lifecycle_policies, prefix, creds)

    async def query_bucket_stats(self, prefix: str) -> list[dict]:
        creds = await self._get_credentials()
        return await asyncio.to_thread(self._s3_query_bucket_stats, prefix, creds)

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

    # -- Local-mode pipeline staging (parity with the GCS adapter) ------------

    async def _local_stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        os.makedirs(working_dir, exist_ok=True)
        staged_paths = []
        for record in file_records:
            filename = record.get("filename", f"file-{uuid.uuid4().hex[:8]}")
            src = record.get("local_path") or record.get("storage_uri") or record.get("gcs_uri", "")
            dest = os.path.join(working_dir, filename)
            if src and os.path.exists(src):
                shutil.copy2(src, dest)
            else:
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
                            "storage_uri": f"s3://{self.results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}/{fname}",
                            "size_bytes": os.path.getsize(dest),
                        }
                    )
        return collected

    def _local_storage_metrics(self) -> dict:
        from app.config import settings

        total_monthly = settings.local_storage_cost_monthly
        # Distribute proportionally across buckets (raw ~55%, working ~27%, results ~18%),
        # parity with the GCS adapter's local metrics.
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
                    "storage_class": "STANDARD_IA",
                    "cost_monthly_usd": 0.0,
                },
            ],
            "total_size_gb": 4.51,
            "total_cost_monthly_usd": total_monthly,
        }

    async def _read_storage_config(self) -> dict[str, str]:
        """Read storage_deployed + the per-store bucket-name keys (parity with GCS)."""
        from app.database import async_session_factory
        from app.platform.platform_config_service import PlatformConfigService

        keys = ["storage_deployed", *[f"{s.value}_bucket_name" for s in _METRICS_STORES]]
        async with async_session_factory() as session:
            return await PlatformConfigService.get_many(session, keys)

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

    def _s3_read_prefix(self, uri: str, length: int, creds) -> bytes:
        from botocore.exceptions import ClientError

        bucket, key = self._parse_uri(uri)
        try:
            # The HTTP range is inclusive at both ends, and S3 serves what it
            # has when the object is shorter.
            resp = self._get_s3_client(creds).get_object(
                Bucket=bucket, Key=key, Range=f"bytes=0-{max(length - 1, 0)}"
            )
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

    async def _s3_storage_metrics(self) -> dict:
        config = await self._read_storage_config()
        if config.get("storage_deployed", "false") != "true":
            raise ValidationError("Storage infrastructure has not been deployed yet")
        creds = await self._get_credentials()
        raw = await asyncio.to_thread(self._s3_collect_bucket_metrics, config, creds)
        buckets: list[dict] = []
        total_gb = 0.0
        total_cost = 0.0
        for name, size_bytes, object_count, storage_class in raw:
            size_gb = size_bytes / (1024**3)
            cost = round(size_gb * _S3_STANDARD_COST_PER_GB, 2)
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

    def _s3_collect_bucket_metrics(self, config: dict, creds) -> list[tuple]:
        """Enumerate configured buckets and return (name, size_bytes, count, class).

        Sums object sizes via list_objects_v2 (streamed by paginator), the S3
        analog of the GCS adapter's blob enumeration. S3 has no bucket-level storage
        class (it is per-object), so the bucket class is reported as STANDARD; a
        CloudWatch BucketSizeBytes path is a later optimization.
        """
        client = self._get_s3_client(creds)
        out: list[tuple] = []
        for store in _METRICS_STORES:
            bucket_name = config.get(f"{store.value}_bucket_name", "")
            if not bucket_name or bucket_name == "null":
                continue
            total_size = 0
            count = 0
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket_name):
                for obj in page.get("Contents", []):
                    total_size += obj.get("Size", 0)
                    count += 1
            out.append((bucket_name, total_size, count, "STANDARD"))
        return out

    def _s3_lifecycle_summaries(self, client, bucket_name: str) -> list[str]:
        from botocore.exceptions import ClientError

        try:
            rules = client.get_bucket_lifecycle_configuration(Bucket=bucket_name).get("Rules", [])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
                return []
            raise
        return _summarize_s3_lifecycle(rules)

    def _s3_bucket_admin_metrics(self, bucket_name: str, creds) -> BucketAdminMetrics:
        client = self._get_s3_client(creds)
        total_size = 0
        count = 0
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []):
                total_size += obj.get("Size", 0)
                count += 1
        versioning = client.get_bucket_versioning(Bucket=bucket_name).get("Status") == "Enabled"
        return BucketAdminMetrics(
            size_bytes=total_size,
            object_count=count,
            # S3 has no bucket-level storage class (it is per-object); created_at is
            # not available from a single bucket call (it needs ListBuckets), so it
            # is left None here.
            storage_class="STANDARD",
            versioning_enabled=versioning,
            lifecycle_summaries=self._s3_lifecycle_summaries(client, bucket_name),
            created_at=None,
        )

    def _s3_delete_bucket(self, bucket_name: str, creds) -> None:
        client = self._get_s3_client(creds)
        # Purge every object plus all noncurrent versions + delete markers (each page
        # holds <=1000, the delete_objects batch limit), then delete the empty bucket.
        for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket_name):
            to_delete = [
                {"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in (*page.get("Versions", []), *page.get("DeleteMarkers", []))
            ]
            if to_delete:
                client.delete_objects(Bucket=bucket_name, Delete={"Objects": to_delete})
        client.delete_bucket(Bucket=bucket_name)

    def _s3_list_lifecycle_policies(self, prefix: str, creds) -> list[dict]:
        client = self._get_s3_client(creds)
        policies: list[dict] = []
        # S3 ListBuckets has no server-side prefix filter, so match on bucket name.
        for bucket in client.list_buckets().get("Buckets", []):
            name = bucket["Name"]
            if prefix and not name.startswith(prefix):
                continue
            rules = self._s3_lifecycle_summaries_raw(client, name)
            policies.append({"bucket_name": name, "rules": rules, "enabled": len(rules) > 0})
        return policies

    def _s3_lifecycle_summaries_raw(self, client, bucket_name: str) -> list[dict]:
        from botocore.exceptions import ClientError

        try:
            return client.get_bucket_lifecycle_configuration(Bucket=bucket_name).get("Rules", [])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
                return []
            raise

    def _s3_query_bucket_stats(self, prefix: str, creds) -> list[dict]:
        client = self._get_s3_client(creds)
        results: list[dict] = []
        for bucket in client.list_buckets().get("Buckets", []):
            name = bucket["Name"]
            if prefix and not name.startswith(prefix):
                continue
            total_bytes = 0
            object_count = 0
            by_class: dict[str, int] = {}
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
                for obj in page.get("Contents", []):
                    size = obj.get("Size", 0)
                    total_bytes += size
                    object_count += 1
                    sc = obj.get("StorageClass", "STANDARD")
                    by_class[sc] = by_class.get(sc, 0) + size
            results.append(
                {
                    "name": name,
                    "total_bytes": total_bytes,
                    "object_count": object_count,
                    "by_storage_class": by_class,
                }
            )
        return results
