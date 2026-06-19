"""Abstract base classes for the BioAF Adapter Layer (BAL).

Defines provider interfaces for compute, storage, and notebook operations.
All service-layer code depends on these abstractions, never concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import BinaryIO

from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import (
    CellxgeneInstance,
    ClusterDetail,
    ClusterMetrics,
    ClusterProbe,
    ClusterStatus,
    CostEstimate,
    JobProgress,
    JobStatus,
    BucketAdminMetrics,
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

    async def get_cluster_detail(self) -> ClusterDetail:
        """Cluster name/status/node-count plus per-pool detail, status pre-mapped.

        Richer than ``get_cluster_status`` and used by the stack-status view.
        Backends without a managed control plane (SLURM) do not implement it;
        the default raises so callers fall back (the stack view reports no
        cluster). Non-abstract so the interface can grow without a flag day.
        """
        raise NotImplementedError

    # -- Cluster lifecycle management (orphan scan / recovery / teardown) ------
    #
    # Look up, probe, and DELETE clusters by (project/account, location, name).
    # Used by orphaned-resource cleanup; the managed-control-plane SDK lives in
    # the adapter. Backends without a managed control plane (SLURM) raise.

    async def list_cluster_names(self, project_id: str, location: str) -> list[str]:
        """Names of all clusters under ``(project_id, location)``."""
        raise NotImplementedError

    async def probe_cluster(self, project_id: str, location: str, cluster_name: str) -> ClusterProbe:
        """Probe one named cluster's liveness + connection info (for adoption).

        Returns ``ClusterProbe(state="NOT_FOUND")`` if it cannot be fetched.
        """
        raise NotImplementedError

    async def delete_cluster(self, project_id: str, location: str, cluster_name: str) -> None:
        """Delete a cluster by name. DESTRUCTIVE and irreversible."""
        raise NotImplementedError

    @abstractmethod
    async def get_cost_estimate(self, job_spec: dict) -> CostEstimate:
        """Estimate cost for a job spec."""

    @abstractmethod
    async def get_job_progress(self, job_id: str) -> JobProgress:
        """Get normalized progress for a running job (percent_complete + processes)."""

    @abstractmethod
    async def get_connection_command(self, job_id: str) -> str:
        """Get kubectl exec/SSH command for direct access to a running job."""

    def connection_setup_guide(self) -> str:
        """First-time client setup instructions to accompany a connection command.

        Cloud/compute-specific (a GKE cluster needs gcloud + kubectl creds; an EKS
        cluster needs aws). The default is the cloud-neutral SLURM/SSH guidance;
        the Kubernetes backends override with the cluster-access steps.
        """
        return "For SLURM-based clusters, ensure SSH access is configured with your system administrator."

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

    def sync_in_command(self, remote_prefix: str, local_dir: str) -> list[str]:
        """Shell command (argv) to recursively sync ``remote_prefix`` -> ``local_dir``.

        The directory-mirror counterpart to ``cli_copy_in`` (single object). Run in
        a stage-in init container. GCS -> ``gsutil -m rsync -r`` (tolerant of an
        empty/missing prefix); S3 -> ``aws s3 sync``; NFS -> ``cp -r``.
        """
        raise NotImplementedError

    def sync_out_command(self, local_dir: str, remote_prefix: str) -> list[str]:
        """Shell command (argv) to recursively sync ``local_dir`` -> ``remote_prefix``.

        The directory-mirror counterpart to ``cli_copy_out``. Run at session
        shutdown to persist a home/outputs dir. GCS -> ``gsutil -m rsync -r``;
        S3 -> ``aws s3 sync``; NFS -> ``cp -r``.
        """
        raise NotImplementedError

    def staging_image(self) -> str:
        """Container image whose CLI stages data for this backend (CopyStager).

        The init/sidecar containers that run ``cli_copy_in`` / ``cli_copy_out``
        need an image that ships the backend's CLI. GCS -> ``google/cloud-sdk:slim``
        (gsutil/gcloud); S3 -> ``amazon/aws-cli``; a mounted NFS backend stages by
        plain ``cp`` and needs only a minimal coreutils image. Lives here so the
        k8s adapters stop hardcoding the cloud's staging image.
        """
        raise NotImplementedError

    def input_mount_spec(
        self, *, name: str, bucket: str, mount_path: str, key_prefix: str = ""
    ) -> tuple[dict, dict, dict]:
        """Read-only object FUSE mount for INPUTS only (ReadOnlyInputMount seam).

        Returns ``(volume, volume_mount, pod_annotations)`` for a pod that streams
        objects from ``bucket`` (optionally under ``key_prefix``) read-only at
        ``mount_path``. GCS -> gcsfuse CSI volume + the ``gke-gcsfuse/volumes``
        pod annotation; S3 -> Mountpoint-S3 CSI (readOnly); NFS -> a plain nfs
        volume. NEVER the workDir (that is a POSIX scratch volume, not an object
        mount). Backends without an object FUSE mount do not implement this.
        """
        raise NotImplementedError

    def nextflow_scratch_directives(self, work_dir: str) -> list[str]:
        """Nextflow config lines for the pipeline scratch workDir (ScratchWorkDir).

        The workDir is where Nextflow exchanges ``.command.run`` scripts and task
        I/O across head/process pods, so it must behave POSIX. GCS overlays the
        ``gs://`` workDir with Wave+Fusion (which mounts it as a local filesystem
        in each task pod); a mounted POSIX backend (NFS, or an AWS EBS/EFS PVC in
        Stage 6e) is the workDir directly with no Fusion overlay. Returns the
        backend-specific config directives so the compute adapter names no cloud.
        """
        raise NotImplementedError

    def image_storage_pip_packages(self) -> str:
        """Pip requirement string for the client libs a built image needs for this store.

        Baked into pipeline/notebook image recipes so a container can read/write the
        backend. GCS -> ``google-cloud-storage gsutil``; S3 -> ``boto3 awscli``; a
        mounted NFS backend needs none.
        """
        raise NotImplementedError

    def cloud_build_copy_step(self, uri: str, dest: str) -> dict:
        """A managed-build step that copies object ``uri`` to local ``dest``.

        Returned to the cloud's managed image-builder (GCP Cloud Build today) as a
        step that stages a file into the build workspace. GCS uses the gsutil
        builder; the AWS realization (a CodeBuild phase) lands with the image-build
        seam. The cloud-specific builder/CLI lives here, not in the service layer.
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

    # -- Bucket-admin enumeration (Tier-2 / Phase 9) --------------------------
    #
    # Rich bucket-level enumeration (per-bucket size/lifecycle/versioning, and
    # project-level bucket listing). The cloud SDK that walks buckets lives in
    # the adapter; the service layer consumes neutral results. Non-abstract so
    # backends without the concept (NFS) keep the degenerate default.

    async def get_bucket_admin_metrics(self, bucket_name: str) -> BucketAdminMetrics:
        """Return rich admin metrics for a single named bucket.

        Backends without buckets (NFS) return the degenerate default.
        """
        return BucketAdminMetrics()

    async def delete_bucket(self, bucket_name: str) -> None:
        """Delete a bucket and ALL of its contents. DESTRUCTIVE and irreversible.

        Used by orphaned-resource cleanup. Backends without buckets (NFS) raise.
        """
        raise NotImplementedError

    def native_upload_client(self, credentials=None):
        """Return the backend's raw, SYNCHRONOUS object-store client.

        TRANSITIONAL escape hatch for callers that still need direct SDK access
        rather than the neutral async object methods: the reference-data upload
        machinery (resumable-session minting, blob enumeration) and the half-built
        reference URL importer, which streams resumable uploads from a worker
        thread (memory: do not build out the importer in isolation). It keeps the
        cloud SDK import inside ``adapters/`` while those callers await a proper
        seam. Remove when they move onto the neutral methods. Backends without a
        native client (NFS) raise.
        """
        raise NotImplementedError

    async def list_lifecycle_policies(self, prefix: str) -> list[dict]:
        """List lifecycle policy status for buckets matching ``prefix``.

        Each entry is ``{"bucket_name", "rules", "enabled"}``. Backends without
        buckets (NFS) return ``[]``.
        """
        return []

    async def query_bucket_stats(self, prefix: str) -> list[dict]:
        """Per-bucket usage for buckets matching ``prefix``.

        Each entry is ``{"name", "total_bytes", "object_count", "by_storage_class"}``.
        Backends without buckets (NFS) return ``[]``.
        """
        return []


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

    def connection_setup_guide(self) -> str:
        """First-time client setup instructions to accompany a connection command.

        See ``ComputeProvider.connection_setup_guide``. Default is SLURM/SSH; the
        Kubernetes notebook backend overrides with the cluster-access steps.
        """
        return "For SLURM-based clusters, ensure SSH access is configured with your system administrator."

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

    async def load_config(self, force: bool = False) -> dict:
        """Eagerly load + cache this backend's platform_config (called at registry init).

        The cloud-neutral name the registry calls on whichever VM backend is
        active (GCE reads its ``gcp_*`` config, EC2 its ``aws_*`` config). Default
        no-op so a backend that needs no startup config is safe.
        """
        return {}

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
    async def deploy(self, publication_id: int, storage_uri: str, dataset_name: str) -> CellxgeneInstance:
        """Deploy a cellxgene instance for an h5ad dataset at ``storage_uri``.

        ``storage_uri`` is the dataset's neutral storage URI (``gs://`` on GCP,
        ``s3://`` on AWS). Returns a CellxgeneInstance; backend specifics (pod
        name, namespace) live in its provider_details.
        """

    @abstractmethod
    async def teardown(self, publication_id: int) -> CellxgeneInstance:
        """Tear down a cellxgene instance. Returns a CellxgeneInstance (stopped)."""

    @abstractmethod
    async def get_status(self, publication_id: int) -> CellxgeneInstance:
        """Get the status of a cellxgene instance."""
