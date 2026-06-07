"""Abstract base classes for the BioAF Adapter Layer (BAL).

Defines provider interfaces for compute, storage, and notebook operations.
All service-layer code depends on these abstractions, never concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO

from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import (
    CellxgeneInstance,
    ClusterMetrics,
    ClusterStatus,
    CostEstimate,
    JobProgress,
    JobStatus,
    JobSubmitResult,
    ObjectMetadata,
    SessionInfo,
    SessionStatus,
    StorageMetrics,
    StoredObject,
    StorageStore,
    TerminationResult,
    VmInfo,
    VmStatus,
)


class ComputeProvider(ABC):
    """Abstract interface for compute backends (Kubernetes, SLURM)."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing.

        Stub backends (e.g. the SLURM compute stub) rely on this default to
        honestly declare every capability unsupported until implemented. Real
        adapters override to declare the flags they truly support.
        """
        return ProviderCapabilities()

    async def load_cluster_config(self) -> None:
        """Optionally pre-load backend config at startup. No-op by default.

        The registry calls this once on every adapter at startup so adapters
        that need eager config (e.g. the K8s adapters reading GKE cluster
        details) get it without the registry sniffing for the method.
        """
        return None

    @abstractmethod
    async def submit_job(self, job_spec: dict) -> JobSubmitResult:
        """Submit a pipeline job. Returns a JobSubmitResult."""

    @abstractmethod
    async def cancel_job(self, job_id: str) -> JobStatus:
        """Cancel a running or queued job. Returns the resulting JobStatus."""

    @abstractmethod
    async def get_job_status(self, job_id: str) -> JobStatus:
        """Get normalized job status: queued, running, completed, failed, cancelled."""

    @abstractmethod
    async def list_jobs(self, filters: dict | None = None) -> list[JobStatus]:
        """List jobs with optional filtering."""

    @abstractmethod
    async def get_job_logs(self, job_id: str) -> str:
        """Retrieve stdout/stderr for a job."""

    @abstractmethod
    async def get_cluster_status(self) -> ClusterStatus:
        """Get cluster status: node count, capacity, queue depth, health."""

    @abstractmethod
    async def get_cluster_metrics(self) -> ClusterMetrics:
        """Get cluster metrics: CPU, memory, cost rate."""

    @abstractmethod
    async def get_cost_estimate(self, job_spec: dict) -> CostEstimate:
        """Estimate cost for a job spec."""

    @abstractmethod
    async def get_job_progress(self, job_id: str) -> JobProgress:
        """Get normalized progress for a running job (percent_complete + processes)."""

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

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

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
    async def collect_outputs(self, working_dir: str, pipeline_run: dict) -> list[StoredObject]:
        """Move outputs to permanent storage. Returns a list of StoredObject."""

    @abstractmethod
    async def get_storage_metrics(self) -> StorageMetrics:
        """Get storage usage and cost metrics."""

    # -- Object-store interface (Phase 3) -------------------------------------
    #
    # These operate on opaque storage URIs (``gs://...`` today, ``s3://...`` or
    # an NFS path later). Callers never construct a backend client. The default
    # implementations raise NotImplementedError so a new backend must override
    # each one (the NFS stub does, until Phase 7). They are not @abstractmethod
    # so the interface can grow without a flag-day break of every subclass.

    async def resolve_uri(self, store: StorageStore, key: str) -> str:
        """Resolve a logical store + key to a concrete storage URI for writes.

        e.g. ``(StorageStore.INGEST, "uploads/x.fastq") -> gs://<ingest>/uploads/x.fastq``.
        The bucket/export behind each store comes from platform config, so
        callers never name a bucket.
        """
        raise NotImplementedError

    async def read_text(self, uri: str, *, encoding: str = "utf-8") -> str:
        """Download an object and decode it as text. Raises StorageObjectNotFound."""
        raise NotImplementedError

    async def read_bytes(self, uri: str) -> bytes:
        """Download an object as bytes. Raises StorageObjectNotFound."""
        raise NotImplementedError

    async def write_text(self, uri: str, text: str, *, content_type: str = "text/plain") -> None:
        """Upload text to an object, replacing any existing object."""
        raise NotImplementedError

    async def write_bytes(
        self, uri: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        """Upload bytes to an object, replacing any existing object."""
        raise NotImplementedError

    async def upload_file(self, uri: str, file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        """Stream a file-like object to an object (does not buffer the whole body)."""
        raise NotImplementedError

    async def upload_filename(self, uri: str, local_path: str, *, content_type: str | None = None) -> None:
        """Upload a local file to an object."""
        raise NotImplementedError

    async def download_to_file(self, uri: str, file_obj: BinaryIO) -> None:
        """Stream an object into a file-like object (does not buffer the whole body)."""
        raise NotImplementedError

    async def download_to_filename(self, uri: str, local_path: str) -> None:
        """Download an object to a local file path."""
        raise NotImplementedError

    async def delete(self, uri: str) -> None:
        """Delete an object. Idempotent: a missing object is not an error."""
        raise NotImplementedError

    async def exists(self, uri: str) -> bool:
        """Return True if the object exists."""
        raise NotImplementedError

    async def list_objects(
        self,
        uri_prefix: str,
        *,
        recursive: bool = True,
        include_versions: bool = False,
        max_results: int | None = None,
    ) -> list[StoredObject]:
        """List objects under a URI prefix as StoredObject records."""
        raise NotImplementedError

    async def copy(self, source_uri: str, dest_uri: str) -> str:
        """Copy an object to a new URI. Returns the destination URI."""
        raise NotImplementedError

    async def move(self, source_uri: str, dest_uri: str) -> str:
        """Move an object (copy, verify, then delete source). Returns the dest URI.

        Fail-safe: if the copy or its verification fails, the source is left
        intact and an error is raised.
        """
        raise NotImplementedError

    async def get_object_metadata(self, uri: str) -> ObjectMetadata:
        """Return size/checksum/content-type for an object without downloading it.

        Raises StorageObjectNotFound if the object is absent.
        """
        raise NotImplementedError

    async def generate_signed_url(
        self,
        uri: str,
        *,
        method: str = "GET",
        expiry_seconds: int = 3600,
        content_type: str | None = None,
    ) -> str:
        """Mint a time-limited signed URL for direct client access to an object.

        Backends without this ability (NFS) declare ``signed_url_upload=False``
        and raise CapabilityNotSupported. GCS/S3 support it.
        """
        raise NotImplementedError


class NotebookProvider(ABC):
    """Abstract interface for notebook session backends (Kubernetes, SLURM)."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

    async def load_cluster_config(self) -> None:
        """Optionally pre-load backend config at startup. No-op by default."""
        return None

    @abstractmethod
    async def launch_session(self, session_spec: dict) -> SessionInfo:
        """Start a Jupyter/RStudio session. Returns a SessionInfo."""

    @abstractmethod
    async def terminate_session(self, session_id: str, **kwargs) -> TerminationResult:  # type: ignore[override]
        """Stop a running session. Returns a TerminationResult."""

    @abstractmethod
    async def get_session_status(self, session_id: str) -> SessionStatus:
        """Get session health and resource usage."""

    @abstractmethod
    async def list_sessions(self, filters: dict | None = None) -> list[SessionStatus]:
        """List active and recent sessions."""

    @abstractmethod
    async def get_connection_command(self, session_id: str) -> str:
        """Get SSH/exec command for direct access to the session."""


class WorkNodeProvider(ABC):
    """Abstract interface for work node VM backends (GCE)."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

    @abstractmethod
    async def launch_vm(self, vm_spec: dict) -> VmInfo:
        """Create and start a work-node VM. Returns a VmInfo."""

    @abstractmethod
    async def terminate_vm(self, instance_name: str, zone: str, **kwargs) -> TerminationResult:
        """Sync outputs, then stop and delete a VM. Returns a TerminationResult."""

    @abstractmethod
    async def get_vm_status(self, instance_name: str, zone: str) -> VmStatus:
        """Get VM status and external IP."""

    @abstractmethod
    async def list_vms(self, filters: dict | None = None) -> list[VmStatus]:
        """List active work node VMs."""


class CellxgeneProvider(ABC):
    """Abstract interface for cellxgene visualization backends."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

    async def load_cluster_config(self) -> None:
        """Optionally pre-load backend config at startup. No-op by default."""
        return None

    @abstractmethod
    async def deploy(self, publication_id: int, gcs_uri: str, dataset_name: str) -> CellxgeneInstance:
        """Deploy a cellxgene instance for an h5ad dataset.

        Returns a CellxgeneInstance; backend specifics (pod name, namespace)
        live in its provider_details.
        """

    @abstractmethod
    async def teardown(self, publication_id: int) -> CellxgeneInstance:
        """Tear down a cellxgene instance. Returns a CellxgeneInstance (stopped)."""

    @abstractmethod
    async def get_status(self, publication_id: int) -> CellxgeneInstance:
        """Get the status of a cellxgene instance."""
