"""Abstract base classes for the BioAF Adapter Layer (BAL).

Defines provider interfaces for compute, storage, and notebook operations.
All service-layer code depends on these abstractions, never concrete implementations.
"""

from abc import ABC, abstractmethod


class ComputeProvider(ABC):
    """Abstract interface for compute backends (Kubernetes, SLURM)."""

    @abstractmethod
    async def submit_job(self, job_spec: dict) -> dict:
        """Submit a pipeline job. Returns dict with job_id and estimated_cost."""

    @abstractmethod
    async def cancel_job(self, job_id: str) -> dict:
        """Cancel a running or queued job. Returns confirmation dict."""

    @abstractmethod
    async def get_job_status(self, job_id: str) -> dict:
        """Get normalized job status: queued, running, completed, failed, cancelled."""

    @abstractmethod
    async def list_jobs(self, filters: dict | None = None) -> list[dict]:
        """List jobs with optional filtering."""

    @abstractmethod
    async def get_job_logs(self, job_id: str) -> str:
        """Retrieve stdout/stderr for a job."""

    @abstractmethod
    async def get_cluster_status(self) -> dict:
        """Get cluster status: node count, capacity, queue depth, health."""

    @abstractmethod
    async def get_cluster_metrics(self) -> dict:
        """Get cluster metrics: CPU, memory, cost rate."""

    @abstractmethod
    async def get_cost_estimate(self, job_spec: dict) -> dict:
        """Estimate cost for a job spec with confidence interval."""

    @abstractmethod
    async def get_job_progress(self, job_id: str) -> dict:
        """Get normalized progress for a running job.

        Returns dict with percent_complete (float) and processes (list of dicts
        with name, status, cpu, memory_gb, duration_s). Each adapter handles
        engine-specific parsing internally.
        """

    @abstractmethod
    async def get_connection_command(self, job_id: str) -> str:
        """Get kubectl exec/SSH command for direct access to a running job."""

    async def persist_job_logs(self, job_id: str) -> bool:
        """Persist job logs to durable storage before the pod is cleaned up.

        Returns True if logs were successfully persisted. Default
        implementation is a no-op for backends that don't need it.
        """
        return False

    def get_raw_bucket_name(self) -> str:
        """Return the raw data bucket name, or empty string if unavailable."""
        return ""


class StorageProvider(ABC):
    """Abstract interface for storage backends (GCS, NFS)."""

    @abstractmethod
    async def resolve_input_path(self, file_record: dict) -> str:
        """Resolve the path a pipeline container uses for input."""

    @abstractmethod
    async def resolve_output_path(self, pipeline_run: dict, filename: str) -> str:
        """Resolve the path for writing pipeline output."""

    @abstractmethod
    async def stage_inputs(self, file_records: list[dict], working_dir: str) -> list[str]:
        """Prepare input files for a pipeline run. Returns list of local paths."""

    @abstractmethod
    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[dict]:
        """Move outputs to permanent storage. Returns list of file records."""

    @abstractmethod
    async def get_storage_metrics(self) -> dict:
        """Get storage usage and cost metrics."""


class NotebookProvider(ABC):
    """Abstract interface for notebook session backends (Kubernetes, SLURM)."""

    @abstractmethod
    async def launch_session(self, session_spec: dict) -> dict:
        """Start a Jupyter/RStudio session. Returns session_id and URL."""

    @abstractmethod
    async def terminate_session(self, session_id: str, **kwargs) -> dict:  # type: ignore[override]
        """Stop a running session. Returns confirmation dict."""

    @abstractmethod
    async def get_session_status(self, session_id: str) -> dict:
        """Get session health and resource usage."""

    @abstractmethod
    async def list_sessions(self, filters: dict | None = None) -> list[dict]:
        """List active and recent sessions."""

    @abstractmethod
    async def get_connection_command(self, session_id: str) -> str:
        """Get SSH/exec command for direct access to the session."""


class WorkNodeProvider(ABC):
    """Abstract interface for work node VM backends (GCE)."""

    @abstractmethod
    async def launch_vm(self, vm_spec: dict) -> dict:
        """Create and start a GCE VM. Returns dict with instance_name, zone, status, external_ip."""

    @abstractmethod
    async def terminate_vm(self, instance_name: str, zone: str, **kwargs) -> dict:
        """Sync outputs, then stop and delete a VM. Returns output_files and gcs_output_prefix."""

    @abstractmethod
    async def get_vm_status(self, instance_name: str, zone: str) -> dict:
        """Get VM status and external IP."""

    @abstractmethod
    async def list_vms(self, filters: dict | None = None) -> list[dict]:
        """List active work node VMs."""


class CellxgeneProvider(ABC):
    """Abstract interface for cellxgene visualization backends."""

    @abstractmethod
    async def deploy(self, publication_id: int, gcs_uri: str, dataset_name: str) -> dict:
        """Deploy a cellxgene instance for an h5ad dataset.

        Returns dict with pod_name, namespace, status, and access_url.
        """

    @abstractmethod
    async def teardown(self, publication_id: int) -> dict:
        """Tear down a cellxgene instance. Returns confirmation dict."""

    @abstractmethod
    async def get_status(self, publication_id: int) -> dict:
        """Get the status of a cellxgene instance."""
