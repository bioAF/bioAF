# ADR-020: BioAF Adapter Layer (BAL)

**Status:** Superseded by [ADR-065](ADR-065-bal-normalized-contract.md)
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

bioAF's original architecture hardcoded specific infrastructure choices: SLURM for compute orchestration, NFS Filestore for shared storage, and tightly coupled UI components that rendered provider-specific details directly. Feedback from computational biologists revealed that while SLURM is the incumbent in bioinformatics HPC, teams starting fresh would prefer Kubernetes for its lower cost, native GCP/AWS/Azure support, and larger ecosystem. Similarly, GCS buckets are dramatically cheaper than NFS Filestores and sufficient for most pipeline workloads when paired with a download-to-container, upload-results pattern.

Rather than replacing one hardcoded choice with another, bioAF needs an abstraction layer that allows multiple infrastructure backends to coexist behind a unified interface. This enables the platform to recommend the modern path (Kubernetes + GCS) while still supporting the legacy path (SLURM + NFS) for teams that prefer it.

---

## Decision

Introduce the **BioAF Adapter Layer (BAL)**: a set of interface contracts that decouple bioAF's application logic and UI from specific infrastructure providers. The BAL defines normalized data models and operations for three provider categories: compute, storage, and interactive analysis (notebooks). Each category has one or more adapter implementations that translate between the provider's native concepts and bioAF's normalized model.

### Provider Categories and Interface Contracts

### Compute Provider

The compute provider interface defines operations for running batch workloads (pipeline jobs) and managing the compute cluster.

| Operation | Description | Returns |
|---|---|---|
| `submit_job(job_spec)` | Submit a pipeline job for execution | Job ID, estimated cost |
| `cancel_job(job_id)` | Cancel a running or queued job | Confirmation |
| `get_job_status(job_id)` | Get current status of a job | Normalized status (queued, running, completed, failed, cancelled) |
| `list_jobs(filters)` | List jobs with filtering | List of normalized job records |
| `get_job_logs(job_id)` | Retrieve stdout/stderr | Log content |
| `get_cluster_status()` | Get overall cluster health | Normalized cluster status (node count, capacity, queue depth, health) |
| `get_cluster_metrics()` | Get real-time resource metrics | Normalized metrics (CPU, memory, cost rate) |
| `get_cost_estimate(job_spec)` | Estimate cost for a job before submission | Cost estimate with confidence interval |

### Storage Provider

The storage provider interface defines operations for managing data files used by pipelines and analysis.

| Operation | Description | Returns |
|---|---|---|
| `resolve_input_path(file_record)` | Get the path/URI a pipeline container uses to access an input file | Provider-specific path |
| `resolve_output_path(pipeline_run, filename)` | Get the path/URI for writing a pipeline output | Provider-specific path |
| `stage_inputs(file_records, working_dir)` | Prepare input files for a pipeline run (download, mount, or symlink) | List of local paths |
| `collect_outputs(working_dir, pipeline_run)` | Move outputs to permanent storage and register in metadata DB | List of file records |
| `get_storage_metrics()` | Get storage usage and cost | Normalized metrics (used, cost, breakdown by tier) |

### Notebook Provider

The notebook provider interface defines operations for launching and managing interactive analysis sessions.

| Operation | Description | Returns |
|---|---|---|
| `launch_session(session_spec)` | Start a Jupyter or RStudio session | Session ID, access URL |
| `terminate_session(session_id)` | Stop a running session | Confirmation |
| `get_session_status(session_id)` | Get session health and resource usage | Normalized status |
| `list_sessions(filters)` | List active and recent sessions | List of normalized session records |
| `get_connection_command(session_id)` | Get SSH/exec command for direct access | Command string |

### Adapter Selection

The compute stack choice is made during initial platform setup and stored in the `platform_config` table. The setup wizard presents two options:

- **Kubernetes + GCS (recommended):** Pipeline jobs run as Kubernetes Jobs on GKE. Data stored in GCS buckets. Lower cost, native GCP autoscaling, cloud-native architecture.
- **SLURM + NFS:** Pipeline jobs submitted to SLURM batch scheduler. Data stored on NFS Filestore. Traditional HPC architecture familiar to bioinformatics teams.

Each option displays estimated monthly costs and an information tooltip explaining the tradeoffs. The selection determines which adapter implementations are activated. Changing the compute stack after initial setup is a destructive operation (requires re-provisioning) and is not supported in v1.

### Normalized Data Models

The BAL normalizes provider-specific concepts into common representations that the UI renders consistently:

| bioAF Concept | Kubernetes Mapping | SLURM Mapping |
|---|---|---|
| Cluster node | GKE node | SLURM compute node |
| Node pool / partition | GKE node pool | SLURM partition |
| Job | Kubernetes Job | SLURM batch job |
| Queue depth | Pending pods | Queued jobs per partition |
| Autoscaling capacity | Node pool min/max | Autoscaler min/max nodes |
| Cost rate | GKE node cost per hour | Instance cost per hour |
| Interactive session | Pod running Jupyter/RStudio | SLURM interactive job |

The UI renders dashboard components based on the normalized model. Provider-specific details (e.g., pod restart count for K8s, exit codes for SLURM) are available in detail views but the summary views use normalized fields.

### Implementation Structure

```text
backend/
  adapters/
    __init__.py
    base.py              # Abstract base classes for all provider interfaces
    compute/
      __init__.py
      kubernetes.py      # K8s compute adapter (Phase 12)
      slurm.py           # SLURM compute adapter (stubbed, future)
    storage/
      __init__.py
      gcs.py             # GCS storage adapter (Phase 12)
      nfs.py             # NFS storage adapter (stubbed, future)
    notebooks/
      __init__.py
      kubernetes.py      # K8s notebook spawner (Phase 12)
      slurm.py           # SLURM notebook spawner (stubbed, future)
    registry.py          # Adapter registry - resolves active adapters from platform_config
```

The adapter registry reads the `platform_config.compute_stack` value on startup and instantiates the appropriate adapter implementations. All service-layer code depends on the abstract base classes, never on concrete adapters.

### Terraform Integration

The existing pattern of separate `.tf` files per optional component (ADR-007) extends naturally:

- `compute-k8s.tf` + `compute-slurm.tf` with `count = var.compute_stack == "kubernetes" ? 1 : 0`
- `storage-gcs.tf` + `storage-nfs.tf` with corresponding feature flags
- `terraform.tfvars` includes `compute_stack = "kubernetes"` (or `"slurm"`) set during initial setup

---

## Consequences

**Positive:**

- Teams can choose the infrastructure stack that fits their experience and budget
- The recommended path (K8s + GCS) is significantly cheaper to operate
- Future adapters (AWS EKS, Azure AKS, alternative storage backends) plug into the same interface without changing application logic or UI
- UI components are simpler: they render normalized data without provider-specific conditionals scattered throughout

**Negative:**

- The abstraction layer adds indirection; debugging may require understanding both the normalized model and the provider-specific behavior
- Two implementations of each interface must be maintained (though SLURM is stubbed for now)
- Some provider-specific features (e.g., SLURM's partition-level accounting, K8s pod affinities) are harder to expose through a normalized interface
- The initial implementation only delivers the K8s adapters; SLURM users cannot migrate to bioAF until the SLURM adapters are built

**Neutral:**

- No changes to the data model: the BAL sits between the service layer and infrastructure, not between the service layer and the database
- Pipeline definitions (Nextflow, Snakemake) already support both K8s and SLURM executors, so pipeline authors are unaffected

---

## References

- ADR-002 (mandatory/optional component split)
- ADR-007 (UI-driven Terraform)
- ADR-021 (Kubernetes compute adapter)
- ADR-022 (GCS storage adapter)
