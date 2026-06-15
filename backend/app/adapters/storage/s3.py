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

import os
from urllib.parse import urlparse

from app.adapters.base import StorageProvider
from app.adapters.capabilities import ProviderCapabilities
from app.exceptions import ValidationError
from app.adapters.models import StorageStore

# Local mode emulates the object store on the filesystem, exactly like the GCS
# adapter (``s3://<bucket>/<key>`` maps to LOCAL_DATA_ROOT/_objects/<bucket>/<key>),
# so the same LOCAL_DATA_ROOT layout serves both backends in dev/CI.
LOCAL_DATA_ROOT = os.environ.get("BIOAF_LOCAL_DATA_ROOT", "/tmp/bioaf-data")
_LOCAL_OBJECTS_DIR = "_objects"


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
        return "boto3==1.35.99 awscli==1.41.13"
