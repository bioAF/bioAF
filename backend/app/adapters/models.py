"""Normalized return models for the BioAF Adapter Layer (BAL).

The BAL's methods historically returned untyped ``dict``s whose shape was
defined only by the Kubernetes adapter. These Pydantic models make the contract
explicit and backend-neutral so a second backend (SLURM/NFS) is built to satisfy
a written interface rather than reverse-engineered from dict keys.

Two rules every model follows:

1. **Normalized fields are first-class.** The fields here are the ones core
   logic and the UI may depend on, and they mean the same thing on every
   backend. Where a backend cannot produce a field, it is ``Optional`` and the
   corresponding capability flag is False (see ``capabilities.py``); logic keys
   off the capability, never a null guess.
2. **Backend specifics go in ``provider_details``.** An opaque dict for extras
   that are meaningful only to one backend (K8s pod name, GKE phase, GCS md5).
   Detail/disclosure views may render it; core logic must not branch on it.

Only methods that return a dict / list[dict] get a model. Methods that already
return a plain ``str`` (get_job_logs, get_connection_command, resolve_*_path)
are left as strings.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class JobState(str, Enum):
    """Canonical lifecycle of a batch compute job, identical across backends.

    Matches the status vocabulary pipeline_run / experiment already expect, so
    routing through the BAL introduces no status-machine drift.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ServiceState(str, Enum):
    """Lifecycle of a long-running service resource: notebook sessions, work-node
    VMs, and cellxgene instances all share this vocabulary."""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


def to_service_state(value: str | None) -> ServiceState:
    """Coerce a backend status string to a ServiceState; unrecognized -> UNKNOWN.

    Backends emit raw status strings (e.g. a GCE ``TERMINATED``); this keeps an
    unexpected value from crashing the boundary, mapping it to UNKNOWN instead.
    """
    try:
        return ServiceState(value)
    except ValueError:
        return ServiceState.UNKNOWN


def to_job_state(value: str | None) -> JobState:
    """Coerce a backend status string to a JobState; unrecognized -> FAILED.

    An unknown terminal-ish value is treated as FAILED rather than silently
    dropped, so a job never appears stuck in a non-existent state.
    """
    try:
        return JobState(value)
    except ValueError:
        return JobState.FAILED


# --- Compute -----------------------------------------------------------------


class CostEstimate(BaseModel):
    """Estimated cost of a job. cost_estimation capability gates whether this is
    meaningful (off on SLURM/on-prem)."""

    estimated_cost_usd: float
    currency: str = "USD"
    basis: str = ""
    provider_details: dict = Field(default_factory=dict)


class JobSubmitResult(BaseModel):
    job_id: str
    status: JobState = JobState.QUEUED
    estimated_cost: CostEstimate | None = None
    provider_details: dict = Field(default_factory=dict)


class TerminationReason(BaseModel):
    container: str | None = None
    exit_code: int | None = None
    reason: str | None = None


class JobStatus(BaseModel):
    job_id: str
    status: JobState
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    exit_code: int | None = None
    termination_reasons: list[TerminationReason] = Field(default_factory=list)
    provider_details: dict = Field(default_factory=dict)


class ProcessInfo(BaseModel):
    """One process/task within a pipeline run (e.g. a Nextflow task)."""

    name: str
    status: str
    cpu: float | None = None
    memory_gb: float | None = None
    duration_s: int | None = None
    task_id: str | None = None
    attempt: int | None = None
    exit_code: int | None = None


class JobProgress(BaseModel):
    percent_complete: float
    processes: list[ProcessInfo] = Field(default_factory=list)
    provider_details: dict = Field(default_factory=dict)


class NodePoolStatus(BaseModel):
    name: str
    machine_type: str | None = None
    min_nodes: int | None = None
    max_nodes: int | None = None
    current_nodes: int | None = None
    status: str | None = None
    spot: bool | None = None


class ClusterStatus(BaseModel):
    controller_status: str | None = None
    node_pools: list[NodePoolStatus] = Field(default_factory=list)
    total_nodes: int = 0
    active_nodes: int = 0
    queue_depth: int = 0
    health: str | None = None
    provider_details: dict = Field(default_factory=dict)


class NodePoolMetrics(BaseModel):
    name: str
    cpu_utilization_pct: float | None = None
    memory_utilization_pct: float | None = None
    cost_rate_hourly: float | None = None


class ClusterMetrics(BaseModel):
    cpu_utilization_pct: float | None = None
    memory_utilization_pct: float | None = None
    cost_burn_rate_hourly: float | None = None
    node_pools: list[NodePoolMetrics] = Field(default_factory=list)
    provider_details: dict = Field(default_factory=dict)


# --- Storage -----------------------------------------------------------------


class StoredObject(BaseModel):
    """A single output object recorded after collect_outputs."""

    filename: str
    storage_uri: str
    size_bytes: int | None = None
    md5_hash: str | None = None
    provider_details: dict = Field(default_factory=dict)


class BucketMetrics(BaseModel):
    name: str
    size_gb: float | None = None
    object_count: int | None = None
    storage_class: str | None = None
    cost_monthly_usd: float | None = None


class StorageMetrics(BaseModel):
    buckets: list[BucketMetrics] = Field(default_factory=list)
    total_size_gb: float = 0.0
    total_cost_monthly_usd: float = 0.0
    provider_details: dict = Field(default_factory=dict)


# --- Notebook sessions -------------------------------------------------------


class SessionInfo(BaseModel):
    """Returned from launch_session: how to reach a freshly-launched session."""

    session_id: str
    status: ServiceState
    access_url: str | None = None
    session_type: str | None = None
    created_at: str | None = None
    provider_details: dict = Field(default_factory=dict)


class SessionStatus(BaseModel):
    """Returned from get_session_status / list_sessions items."""

    session_id: str
    status: ServiceState
    access_url: str | None = None
    session_type: str | None = None
    user_id: str | None = None
    provider_details: dict = Field(default_factory=dict)


# --- Work-node VMs -----------------------------------------------------------


class VmInfo(BaseModel):
    instance_name: str
    status: ServiceState
    zone: str | None = None
    access_url: str | None = None
    created_at: str | None = None
    provider_details: dict = Field(default_factory=dict)


class VmStatus(BaseModel):
    instance_name: str
    status: ServiceState
    zone: str | None = None
    external_ip: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    provider_details: dict = Field(default_factory=dict)


class TerminationResult(BaseModel):
    """Returned from terminate_vm / terminate_session: the stopped resource and
    any outputs synced to storage on the way down."""

    status: ServiceState = ServiceState.STOPPED
    output_files: list[StoredObject] = Field(default_factory=list)
    output_prefix: str = ""
    provider_details: dict = Field(default_factory=dict)


# --- Cellxgene ---------------------------------------------------------------


class CellxgeneInstance(BaseModel):
    publication_id: int
    status: ServiceState
    access_url: str | None = None
    provider_details: dict = Field(default_factory=dict)
