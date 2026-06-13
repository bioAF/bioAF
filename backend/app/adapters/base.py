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

    async def load_cluster_config(self, force: bool = False) -> dict | None:
        """Optionally pre-load backend config at startup. No-op by default.

        The registry calls this once on every adapter at startup so adapters
        that need eager config (e.g. the K8s adapters reading GKE cluster
        details) get it without the registry sniffing for the method. Backends
        that load config return it as a dict; ``force`` re-reads past any cache.
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

    async def get_job_report(self, job_id: str) -> str:
        """Return an HTML run report for a job, or '' if the backend has none.

        Gated by the ``job_report`` capability. Backends that produce a report
        artifact (e.g. the Nextflow HTML report on Kubernetes) override this;
        backends without one inherit the empty-string default rather than
        raising, so callers can invoke it on the interface unconditionally.
        """
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

    def build_uri(self, bucket: str, key: str) -> str:
        """Mint a backend URI from an explicit container + key.

        The scheme-neutral counterpart to ``resolve_uri``: use it when the
        container (bucket) is a runtime value (a backup bucket, an event's source
        bucket) rather than one of the logical StorageStores. GCS -> ``gs://...``,
        S3 -> ``s3://...``. A pure string transform with no I/O, so it is sync;
        this is what lets callers stop hardcoding the ``gs://`` scheme.
        """
        raise NotImplementedError

    def parse_uri(self, uri: str) -> tuple[str, str]:
        """Split a backend URI into (container, key). Inverse of ``build_uri``.

        ``gs://bucket/a/b.txt -> ("bucket", "a/b.txt")``. Raises ValidationError
        if ``uri`` is not a URI this backend recognizes.
        """
        raise NotImplementedError

    # -- Container-side CLI staging (Leak 2 drain) ----------------------------
    #
    # These mint SHELL COMMAND STRINGS for a remote pipeline container to stage
    # data via the backend's own CLI (the container has no Python adapter). They
    # are how the service layer stops hardcoding ``gsutil`` / ``gcloud`` /
    # ``aws s3``: the cloud-specific CLI tokens live here in adapters/, selected by
    # the storage backend. GCS -> ``gcloud storage`` / ``gcloud auth ...``; S3 ->
    # ``aws s3``; NFS -> plain ``cp`` (a mounted filesystem, no CLI auth).

    def cli_auth_command(self, key_file: str) -> str:
        """Shell command to authenticate this backend's CLI from a mounted key file.

        GCS -> ``gcloud auth activate-service-account --key-file=<key_file> ...``.
        Backends that authenticate ambiently (NFS mount, S3 instance profile/IRSA)
        return ``""`` (no auth step needed).
        """
        raise NotImplementedError

    def cli_copy_in(self, uri: str, local_path: str) -> str:
        """Shell command to copy a single object ``uri`` to ``local_path`` in a container."""
        raise NotImplementedError

    def cli_copy_out(self, local_path: str, uri: str) -> str:
        """Shell command to recursively copy ``local_path`` to a bucket ``uri`` in a container."""
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

    async def write_bytes(self, uri: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
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

    async def delete(self, uri: str, *, generation: int | None = None) -> None:
        """Delete an object. Idempotent: a missing object is not an error.

        ``generation`` targets a specific object version (for wiping noncurrent
        generations from a versioned store); None deletes the live object.
        """
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

    async def get_bucket_info(self, uri: str) -> dict:
        """Return coarse container-level info (e.g. ``{"versioning_enabled": bool}``).

        Backend-specific and intentionally coarse; backends without the concept
        (NFS) raise or report None. Detailed bucket enumeration/lifecycle is a
        Tier-2 concern (Phase 9), not part of this method.
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

    async def create_resumable_upload_url(
        self,
        uri: str,
        *,
        content_type: str = "application/octet-stream",
        size_bytes: int | None = None,
        origin: str | None = None,
    ) -> str:
        """Create a resumable upload session URL the client PUTs bytes to directly.

        ``origin`` lets the backend accept the cross-origin upload without
        bucket-level CORS config. Backends without direct client upload (NFS)
        declare ``signed_url_upload=False`` and raise CapabilityNotSupported.
        """
        raise NotImplementedError


class NotebookProvider(ABC):
    """Abstract interface for notebook session backends (Kubernetes, SLURM)."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

    async def load_cluster_config(self, force: bool = False) -> dict | None:
        """Optionally pre-load backend config at startup. No-op by default."""
        return None

    @abstractmethod
    async def launch_session(self, session_spec: dict) -> SessionInfo:
        """Start a Jupyter/RStudio session. Returns a SessionInfo."""

    @abstractmethod
    async def terminate_session(self, session_id: str, **kwargs) -> TerminationResult:  # type: ignore[override]
        """Stop a running session. Returns a TerminationResult."""

    @abstractmethod
    async def get_session_status(self, session_id: str, **kwargs) -> SessionStatus:
        """Get session health and resource usage.

        ``**kwargs`` carries backend-specific lookup hints (e.g. the K8s pod
        name / namespace) that some backends need to locate the session.
        """

    @abstractmethod
    async def list_sessions(self, filters: dict | None = None) -> list[SessionStatus]:
        """List active and recent sessions."""

    @abstractmethod
    async def get_connection_command(self, session_id: str) -> str:
        """Get SSH/exec command for direct access to the session."""

    async def sync_session_storage(self, session_id: str, **kwargs) -> None:
        """Best-effort push of a running session's working files to durable storage.

        Used to snapshot a live session on demand. Default no-op for backends
        that don't need it (e.g. a shared-filesystem backend where the working
        dir is already persistent); the K8s backend execs a gsutil rsync inside
        the pod.
        """
        return None


class VmInstance(ABC):
    """Cloud-neutral VM lifecycle primitive (GCE today; EC2 in the AWS build).

    The single VM primitive that the work-node provider, future VM-compute, and
    install-time provisioning consume. A backend (``GceVmInstance`` / future
    ``Ec2VmInstance``) implements provision/delete/inspect/list; the work-node
    provider rides on one and exposes it under the ``WorkNodeProvider`` names the
    service layer uses. Selected per-cloud (POLICY ``work_node``: gce | ec2).
    """

    @abstractmethod
    async def provision(self, vm_spec: dict) -> VmInfo:
        """Create and start a VM. Returns a VmInfo."""

    @abstractmethod
    async def delete(self, instance_name: str, zone: str, **kwargs) -> TerminationResult:
        """Stop and delete a VM. Returns a TerminationResult."""

    @abstractmethod
    async def inspect(self, instance_name: str, zone: str) -> VmStatus:
        """Get VM status and external IP."""

    @abstractmethod
    async def list_instances(self, filters: dict | None = None) -> list[VmStatus]:
        """List managed VMs."""

    async def probe_zone_capacity(self, zones: list[str], machine_type: str = "e2-medium") -> str:
        """Return the first zone in ``zones`` with capacity for ``machine_type``.

        Default raises so a backend that cannot probe capacity fails loudly rather
        than silently. The GCE backend implements it via a throwaway instance insert.
        """
        raise NotImplementedError("This VM backend cannot probe zone capacity")


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

    async def probe_zone_capacity(self, zones: list[str], machine_type: str = "e2-medium") -> str:
        """Return the first zone in ``zones`` with capacity for ``machine_type``.

        Pre-flight check before provisioning so a per-zone stockout does not hang
        the deploy. Backend-specific; the default raises so a backend that cannot
        probe capacity fails loudly rather than silently. The GCE adapter
        implements it via a throwaway instance insert.
        """
        raise NotImplementedError("This work-node backend cannot probe zone capacity")


class CellxgeneProvider(ABC):
    """Abstract interface for cellxgene visualization backends."""

    def capabilities(self) -> ProviderCapabilities:
        """Declare what this backend can do. Default: nothing (see ComputeProvider)."""
        return ProviderCapabilities()

    async def load_cluster_config(self, force: bool = False) -> dict | None:
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
