# ADR-034: Custom Ephemeral Work Nodes

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** Brent (repository owner)

---

## Context

bioAF provides two compute primitives today: pipeline jobs (batch, fire-and-forget via Nextflow/Snakemake) and notebook sessions (interactive, browser-based via RStudio/JupyterHub). Computational biologists frequently need something in between: a long-running, SSH-accessible compute node where they can run scripts, train models, iterate on parameters, and manage processes in `tmux` -- all with access to their experimental data.

Examples of workflows that neither pipelines nor notebooks serve well:

- Training an scVI model for 6 hours on a GPU while monitoring loss curves
- Running a Seurat integration on 500K cells that needs 128GB RAM for 2 hours
- Iterative parameter sweeps: launch script, check results, adjust, relaunch
- Running custom R or Python scripts that require packages not in the default notebook image

Notebook sessions can technically do some of this, but browser-based interfaces suffer from connection timeouts, kernel restarts, and the inability to run persistent background processes. Pipeline jobs are too rigid for interactive, exploratory work.

The solution is an ephemeral work node: a Kubernetes Pod launched on demand, running a user-specified environment (ADR-033), with project data mounted via GCS FUSE, accessible over SSH, and automatically terminated when idle.

---

## Decision

Introduce custom work nodes as a new compute session type. Users select a project, an environment version, a machine type, and the data directories to mount. The platform launches a K8s Pod with SSH access, GCS FUSE data mounts, and a configurable idle timeout. Admins control per-user quotas.

### Launch Flow

```text
User selects:
  1. Project (determines available data directories)
  2. Data directories to mount (from project's pipeline outputs, uploads, shared results)
  3. Environment version (from ADR-033, must be in "ready" status)
  4. Machine type (e.g., n2-standard-4, n2-highmem-16, n1-standard-8-nvidia-tesla-t4)

Platform:
  1. Checks user quota (concurrent running nodes < admin-configured limit)
  2. Creates compute_sessions record (status: pending)
  3. K8s adapter builds Pod spec:
     - Container image from environment version's image_uri
     - GCS FUSE volumes for selected data directories (read-only)
     - GCS FUSE volume for user home directory (read-write)
     - emptyDir volume for /scratch (node-local SSD, read-write)
     - PAM user creation in startup script (reuses ADR-030 session credentials)
     - sshd as the main process
     - Node selector for bioaf-interactive pool (or GPU pool if GPU machine type)
  4. Background loop polls for Pod readiness and LoadBalancer IP
  5. Session transitions to "running" with SSH connection details exposed in UI
```

### Pod Filesystem Layout

| Mount | Source | Mode | Purpose |
| --- | --- | --- | --- |
| `/data/` | GCS FUSE (selected project directories) | Read-only | Pipeline outputs, uploaded files, shared results |
| `/home/<username>/` | GCS FUSE (user's persistent home) | Read-write | Scripts, configs, saved results (persists across sessions) |
| `/scratch/` | `emptyDir` (node-local SSD) | Read-write | Temp files, intermediate computation, working data |

The GCS FUSE CSI driver (first-party GKE addon) mounts selected GCS paths as POSIX-compatible directories. The `fileCacheCapacity` option is set to cache frequently accessed files on the node's local SSD, mitigating FUSE latency for random-read workloads like HDF5.

Users work in `/scratch/` for computation and save results they want to keep to `/home/<username>/` or output them to a designated results path.

### SSH Access

Work nodes run `sshd` as their main process (instead of RStudio Server or JupyterHub). The startup script:

```bash
useradd -m -d /home/<username> -s /bin/bash <username> || true
echo '<username>:<bcrypt_hash>' | chpasswd -e
chown -R <username>:<username> /home/<username>
mkdir -p /run/sshd
exec /usr/sbin/sshd -D
```

This reuses the session credentials system from ADR-030. Users SSH in with the same username and password they configured on their profile page. The UI displays the SSH command:

```bash
ssh <username>@<load-balancer-ip>
```

### Quota and Lifecycle

**Admin-controlled settings:**

- **Concurrent node quota.** Maximum number of work nodes a single user can have running simultaneously. Default: 2. Configured per-organization by admins via the `quotas.configure` permission (ADR-032).
- **Idle timeout.** Duration after last SSH session disconnect before the node auto-terminates. Default: 24 hours. Configured per-organization by admins.

**Idle detection:**

A lightweight heartbeat script runs on every work node as a background process. Every 5 minutes, it checks `/var/run/utmp` for active SSH sessions. If sessions exist, it POSTs a heartbeat to the bioAF API. The backend tracks the last heartbeat timestamp per session. If no heartbeat arrives within the idle timeout window, the background loop terminates the Pod.

The heartbeat also checks for user-owned processes with CPU activity above a minimal threshold, so a long-running computation in `tmux` with no active SSH session still counts as active.

**User controls:**

- Users can stop their work node at any time from the UI
- Users can re-launch a stopped node against the same project and environment. Their GCS FUSE home directory persists, so saved results are still there. The `/scratch/` directory is lost on termination (it is an `emptyDir`).

### Machine Types and GPU Support

The launch UI presents a curated list of machine types appropriate for computational biology workloads:

| Category | Machine Types | Use Case |
| --- | --- | --- |
| Standard | `n2-standard-4`, `n2-standard-8` | Light analysis, data wrangling |
| High-memory | `n2-highmem-8`, `n2-highmem-16`, `n2-highmem-32` | Seurat integration, large datasets |
| GPU | `n1-standard-8` + T4, `n1-standard-16` + V100 | scVI, rapids-singlecell, deep learning |

GPU machine types require a GPU node pool (separate from `bioaf-interactive`). The platform creates this pool via Terraform when GPU types are first enabled by an admin.

### Relationship to Notebook Sessions

Work nodes and notebook sessions are both compute sessions. The `compute_sessions` table (renamed from `notebook_sessions`) stores both, differentiated by `session_type`:

- `rstudio` -- RStudio Server, browser-based (existing)
- `jupyter` -- JupyterHub, browser-based (existing)
- `ssh` -- Work node, SSH-based (new)

All session types share the same lifecycle model (pending, starting, running, stopping, stopped), quota enforcement, and audit logging. The K8s adapter handles the differences in startup script (RStudio Server vs. JupyterHub vs. sshd) and data mounting.

---

## Consequences

**Positive:**

- Comp bios get SSH-accessible compute nodes with their exact environment and data, deployable in minutes
- GCS FUSE avoids copying multi-gigabyte datasets; data is available immediately at mount time
- Quota + idle timeout prevents cost leaks from forgotten nodes
- Reusing session credentials (ADR-030) and environment versions (ADR-033) means no new auth or image systems to build
- The `emptyDir` scratch volume provides fast local I/O for computation without FUSE latency concerns

**Negative:**

- GCS FUSE has higher latency than local disk for random reads. The file cache mitigates this but does not eliminate it. Users doing heavy random I/O (e.g., large SQLite databases) should copy data to `/scratch/` first.
- GPU node pools incur cost even when autoscaling to zero, due to GPU driver daemonsets. Admins should only enable GPU types when actively needed.
- `/scratch/` is lost on termination. Users must save results to their home directory or GCS. A warning is displayed in the UI when stopping a node.

**Neutral:**

- The rename from `notebook_sessions` to `compute_sessions` is a database migration + codebase refactor but does not change the API contract for existing notebook session endpoints
- The heartbeat script adds a lightweight background process to every work node image (installed via the bioaf CLI package, ADR-035)

---

## References

- ADR-030 (session credentials -- PAM auth for SSH login)
- ADR-031 (Cloud Build pipeline -- image build infrastructure)
- ADR-032 (custom RBAC -- `work_nodes.*` and `quotas.*` permissions)
- ADR-033 (versioned environments -- image selection at launch)
- ADR-021 (Kubernetes compute backend -- node pools and pod lifecycle)
- ADR-022 (GCS storage backend -- data lives in GCS buckets)
