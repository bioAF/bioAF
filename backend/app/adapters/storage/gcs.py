"""GCS storage adapter.

Supports local/mock mode for development and real GCS API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.
"""

import logging
import os
import shutil
import uuid

from app.adapters.base import StorageProvider

logger = logging.getLogger("bioaf.adapters.storage.gcs")

LOCAL_DATA_ROOT = os.environ.get("BIOAF_LOCAL_DATA_ROOT", "/tmp/bioaf-data")


class GcsStorageProvider(StorageProvider):
    """GCS storage backend with local mode for development."""

    def __init__(self, org_slug: str = "demo"):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._org_slug = org_slug

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

    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[dict]:
        if self.is_local:
            return await self._local_collect_outputs(working_dir, pipeline_run)
        return await self._gcs_collect_outputs(working_dir, pipeline_run)

    async def get_storage_metrics(self) -> dict:
        if self.is_local:
            return self._local_storage_metrics()
        return await self._gcs_storage_metrics()

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

    def _get_gcs_client(self):
        """Get a Google Cloud Storage client. Tests mock this method."""
        from google.cloud import storage

        return storage.Client()

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

    async def _gcs_storage_metrics(self) -> dict:
        """Delegate to GcsStorageService for live bucket metrics.

        Requires a DB session to read platform_config. Since the BAL adapter
        interface does not pass a session, we create a short-lived one here.
        """
        from app.database import async_session_factory
        from app.services.gcs_storage import GcsStorageService

        async with async_session_factory() as session:
            metrics = await GcsStorageService.get_bucket_metrics(session)

        # Convert to the dict format expected by the adapter interface
        buckets: list[dict[str, object]] = []
        total_gb = 0.0
        total_cost = 0.0
        for m in metrics:
            size_gb = m.size_bytes / (1024**3)
            cost = round(size_gb * 0.026, 2)
            total_gb += size_gb
            total_cost += cost
            buckets.append(
                {
                    "name": m.bucket_name,
                    "size_gb": round(size_gb, 2),
                    "object_count": m.object_count,
                    "storage_class": m.storage_class,
                    "cost_monthly_usd": cost,
                }
            )

        return {
            "buckets": buckets,
            "total_size_gb": round(total_gb, 2),
            "total_cost_monthly_usd": round(total_cost, 2),
        }
