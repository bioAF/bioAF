# ADR-043: Work Nodes Migration from GKE to GCE VMs

**Status:** Accepted
**Date:** 2026-04-19
**Deciders:** Brent (repository owner)
**Supersedes:** ADR-034 (for work nodes only; notebook sessions remain on GKE)

---

## Context

ADR-034 introduced custom work nodes as Kubernetes Pods on GKE, sharing the same adapter (`KubernetesNotebookProvider`), environment images, and infrastructure as notebook sessions. In practice, this coupling creates several problems:

1. **Environment mismatch.** Work node users want native Linux VM environments with conda-managed packages, not Docker containers. Scientists already manage conda environments locally and expect the same workflow on remote compute. Docker images are the right abstraction for notebook sessions (RStudio Server, JupyterHub), but not for SSH-accessible development environments.

2. **Image coupling.** Work node and notebook images share the same Artifact Registry pipeline and the same `environments` table without distinction. A work node environment may need entirely different base packages (tmux, htop, custom CLI tools) than a notebook environment (rstudio-server, jupyterhub). Sharing images forces awkward compromises.

3. **K8s overhead for VMs.** Work nodes are long-running, single-user compute sessions that behave like VMs. Running them as Pods adds K8s orchestration complexity (LoadBalancer Services for SSH, node pool scheduling, GCS FUSE CSI driver) without the benefits K8s provides for short-lived batch jobs or web services.

4. **Missing features.** Scientists need to clone GitHub repositories into their work environment at boot time, and they need a clear MOTD guiding them to inputs, outputs, and scratch space. These are easier to implement on a VM with a startup script than in a K8s Pod spec.

---

## Decision

Migrate work nodes from GKE Pods to GCE VMs. Introduce a separate adapter (`GCEWorkNodeProvider`), independent conda-only environment images built via Packer, a GitHub repos feature for auto-cloning at boot, and a MOTD.

### Compute Target: GCE VMs

Work nodes launch as GCE VM instances via the `compute_v1.InstancesClient` API. Each VM gets:

- A Packer-built GCE image with the conda environment pre-installed (see below)
- An ephemeral external IP for SSH access
- A `bioaf-work-node` network tag for firewall rules
- The notebook runner service account for GCS access
- A startup script that handles user-specific configuration (PAM credentials, SSH keys, repo cloning, data mounts, MOTD)

Notebook sessions (RStudio, Jupyter) remain on GKE unchanged.

### Adapter Architecture

A new `WorkNodeProvider` abstract base class is added alongside the existing `NotebookProvider`. The `GCEWorkNodeProvider` implements it using the GCE API. The adapter registry (`registry.py`) gains a `get_work_node_adapter()` function. Work nodes always use GCE regardless of the `compute_stack` setting.

```text
WorkNodeProvider (ABC)
  ├── launch_vm(vm_spec) -> dict
  ├── terminate_vm(instance_name, zone) -> dict
  ├── get_vm_status(instance_name, zone) -> dict
  └── list_vms(filters) -> list[dict]

GCEWorkNodeProvider (concrete)
  └── Uses google.cloud.compute_v1
```

### Environment Type Separation

The `environments` table gains an `environment_type` column with values `"notebook"` and `"work_node"`. Work node environments only accept `conda` as the `definition_format` (no Dockerfile support). Notebook environments continue to support both formats.

The Environments page in the UI provides filtering by type. When launching a work node, only `work_node` environments are shown.

### VM Images via Packer

Work node environment builds use Packer (run inside Cloud Build) to produce GCE VM images instead of Docker images. The build process:

1. Start a temporary GCE VM from Ubuntu 22.04 LTS
2. Install system packages: openssh-server, gcsfuse, git, tmux, htop, fail2ban, miniconda
3. Copy the user's `environment.yml` and run `conda env create`
4. Configure sshd, install the bioaf heartbeat agent
5. Snapshot to a GCE image named `bioaf-worknode-{env_name}-v{version}-{build}`

The `image_uri` on `EnvironmentVersion` stores the GCE image self-link (`projects/{project}/global/images/{name}`) instead of an Artifact Registry Docker URI. The existing Docker build path for notebook environments is unchanged.

### VM Startup Script

The startup script runs at boot and handles user-specific configuration that cannot be baked into the image:

```bash
# 1. Create PAM user with session credentials (ADR-030)
useradd -m -d /home/<username> -s /bin/bash <username>
echo '<username>:<bcrypt_hash>' | chpasswd -e

# 2. SSH keys for GitHub
mkdir -p /home/<username>/.ssh
echo '<private_key>' > /home/<username>/.ssh/id_rsa
chmod 600 /home/<username>/.ssh/id_rsa
ssh-keyscan github.com >> /home/<username>/.ssh/known_hosts

# 3. Clone selected GitHub repos
mkdir -p /home/<username>/repos
cd /home/<username>/repos
git clone <repo_1_ssh_url> <repo_1_name>
git clone <repo_2_ssh_url> <repo_2_name>

# 4. Mount GCS data (read-only)
mkdir -p /data/<mount_path>
gcsfuse --implicit-dirs --only-dir <mount_path> <bucket> /data/<mount_path>

# 5. Create output and scratch directories
mkdir -p /outputs /scratch
chown <username>:<username> /outputs /scratch

# 6. Activate conda environment in user's shell
echo 'conda activate <env_name>' >> /home/<username>/.bashrc

# 7. Generate MOTD
cat > /etc/motd << 'EOF'
=== bioAF Work Node ===
Input data:     /data/                    (read-only GCS mounts)
Your repos:     /home/<username>/repos/   (cloned from GitHub)
Output files:   /outputs/                 (synced to GCS on stop)
Scratch space:  /scratch/                 (LOST on stop)
Environment:    <env_name> v<version>.<build>
EOF

# 8. Heartbeat agent
echo '<token>' > /etc/bioaf/token
# bioaf heartbeat daemon started via systemd or cron

# 9. Ownership
chown -R <username>:<username> /home/<username>
```

### GitHub Repos Feature

A new `github_repos` table stores user-scoped GitHub repository references:

| Column | Type | Purpose |
| --- | --- | --- |
| `id` | Integer PK | Auto-increment |
| `user_id` | Integer FK | Owner |
| `organization_id` | Integer FK | Org scope |
| `git_ssh_url` | String(500) | Git SSH clone URL (e.g., `git@github.com:owner/repo.git`) |
| `display_name` | String(255) | Human-readable name |
| `created_at` | Timestamp | Creation time |

Unique constraint on `(user_id, git_ssh_url)` prevents duplicates.

Users manage their repos from a section on the Work Nodes page. When launching a work node, users select which repos to clone. The startup script clones them into `/home/<username>/repos/<display_name>/`.

### VM Filesystem Layout

| Path | Source | Mode | Purpose |
| --- | --- | --- | --- |
| `/home/<username>/` | VM local disk | Read-write | User home directory |
| `/home/<username>/repos/` | Git clone at boot | Read-write | Cloned GitHub repositories |
| `/data/` | gcsfuse (selected paths) | Read-only | Pipeline outputs, uploads, shared results |
| `/outputs/` | VM local disk | Read-write | Synced to GCS on stop; registered as tracked outputs |
| `/scratch/` | VM local disk | Read-write | Temporary computation; lost on stop |

### VM Lifecycle

**Launch:** User selects project, data mounts, environment version, GitHub repos, and machine type. Platform creates a GCE VM with the pre-built image and startup script. Background poller checks for VM RUNNING status and external IP, then updates the DB with the SSH connection details.

**Running:** Heartbeat agent reports activity every 5 minutes. VM is accessible via `ssh <username>@<external_ip>`. Conda environment is activated by default.

**Stop:** On user-initiated stop:

1. Run `gsutil -m rsync -r /outputs/ gs://{working_bucket}/sessions/{session_id}/outputs/` on the VM
2. Capture scripts (`.py`, `.R`, `.Rmd`, `.ipynb`) from home directory
3. Register output files (ADR-039 provenance)
4. Move outputs to results bucket (ADR-040 two-phase persistence)
5. Delete the GCE VM

**Heartbeat timeout:** Same as ADR-034. Stale nodes are terminated after the admin-configured idle timeout.

### Data Model Changes

**`environments` table:**

- Add `environment_type` column (String, default `"notebook"`)

**`compute_sessions` table:**

- Add `gce_instance_name` (String, nullable)
- Add `gce_zone` (String, nullable)
- Add `gce_project_id` (String, nullable)
- Add `github_repo_ids` (JSON, nullable)

**New `github_repos` table:** As described above.

### Machine Types

The machine type catalog (ADR-034) is updated to remove K8s node pool references. Machine type names map directly to GCE machine types. GPU types include accelerator metadata for the GCE API.

---

## Consequences

**Positive:**

- Scientists get native Linux VMs with conda environments matching their local workflow. No Docker abstraction layer.
- Packer-built VM images provide fast startup (no conda install at boot).
- GCE VMs are simpler to manage than K8s Pods for long-running SSH sessions. No LoadBalancer Services, no FUSE CSI driver, no node pool scheduling.
- Environment type separation prevents confusion between notebook and work node images.
- GitHub repo auto-cloning eliminates a manual step scientists currently perform on every new compute session.
- MOTD provides immediate orientation on login.

**Negative:**

- Packer builds take longer than Docker builds (VM boot + package install + snapshot vs. container layer caching). Build times of 15-30 minutes are expected for R/Bioconductor-heavy environments.
- GCE VMs with external IPs are exposed to the internet on port 22. Mitigated by: firewall rules restricting to SSH only, bcrypt PAM passwords, fail2ban for brute-force protection.
- Two build pipelines (Docker for notebooks, Packer for work nodes) doubles the build infrastructure surface. Both run through Cloud Build, but the Packer template and Docker template are separate codepaths.
- gcsfuse on GCE requires the VM's service account to have storage permissions, whereas GKE used Workload Identity. The service account attachment is simpler but less granular.

**Neutral:**

- The `WorkNodeProvider` ABC is separate from `NotebookProvider`, keeping each adapter focused. No risk of regressions to notebook sessions.
- Existing work node records in the database (with `k8s_pod_name`) are unaffected. New work nodes populate `gce_instance_name` instead. Both columns are nullable.
- The heartbeat mechanism, quota system, and idle timeout are unchanged in behavior -- only the underlying adapter call changes.

---

## References

- ADR-034 (Custom work nodes -- original K8s-based design, superseded for work nodes)
- ADR-033 (Versioned compute environments -- environment and version model)
- ADR-041 (Environment build versioning -- build numbers and immutable images)
- ADR-030 (Session credentials -- PAM auth reused for VM SSH login)
- ADR-040 (Notebook file lifecycle -- output persistence and two-phase sync)
- ADR-021 (Kubernetes compute backend -- notebook sessions remain here)
- ADR-022 (GCS storage backend -- data lives in GCS buckets)
