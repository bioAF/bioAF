# ADR-026: SSH Access to Running Containers

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

Feedback from computational biologists revealed that many teams author and debug pipelines interactively by SSHing into compute nodes and coding live. While bioAF's architecture encourages version-controlled pipeline definitions imported from GitHub, forcing this workflow from day one creates adoption friction. Teams need to be met where they are, with the platform gently guiding them toward better practices over time.

The immediate need: bioinformaticians want to connect directly to running containers or compute nodes to inspect state, debug failing pipelines, test commands, and iteratively develop new pipelines. bioAF currently provides no mechanism for this: all interaction is through the web UI.

A full web-based IDE (code-server / VS Code in browser) would address this more comprehensively but is a significantly larger engineering effort. SSH/exec access is a pragmatic first step that delivers most of the value with minimal implementation cost.

---

## Decision

Expose connection commands (kubectl exec for Kubernetes, SSH for SLURM) in the bioAF UI for running containers and compute nodes. Record in the audit trail each time terminal instructions are generated (bioAF cannot observe whether anyone then connects). Do not attempt to monitor or restrict what happens inside the session: that responsibility belongs to the user. See the 2026-11-16 amendment below for the shipped behavior.

### Implementation

**For Kubernetes compute backend (ADR-021):**

When a user views a running pipeline job or notebook session, the UI displays a "Connect" button. Clicking it reveals a copyable command:

```bash
kubectl exec -it <pod-name> -n bioaf-pipelines -- /bin/bash
```

The backend generates this command from the pod metadata. The command includes the correct namespace, pod name, and container name (if the pod has multiple containers).

**For SLURM compute backend (future):**

```bash
ssh <username>@<login-node-ip> -t "srun --jobid=<job-id> --pty /bin/bash"
```

Or for direct node access:

```bash
ssh <username>@<compute-node-ip>
```

### Access Control

- Connection commands are available to users whose role grants `notebooks:launch` (amended 2026-11-16: eligibility follows effective permissions, including custom roles, not built-in role names; built-in role defaults are unchanged)
- The connection command inherits the user's platform permissions: the user authenticates with their own credentials or kubeconfig
- For Kubernetes: the user must have `kubectl` installed locally and their kubeconfig configured to access the GKE cluster. The UI provides a one-time setup guide with the `gcloud` command to configure kubeconfig:

```bash
gcloud container clusters get-credentials bioaf-cluster --region <region> --project <project-id>
```

### What is Logged

Every time a user clicks "Connect" and the command is revealed, the following is recorded in the audit log:

| Field | Value |
|---|---|
| `user_id` | The user who requested the connection |
| `entity_type` | `container_session` |
| `entity_id` | Pod name or SLURM job ID |
| `action` | `connection_command_generated` |
| `details_json` | Target pod/node, namespace, associated pipeline run or notebook session, timestamp |

The system does not log what the user does after connecting, and it cannot tell whether the user connected at all: the entry records who was given instructions for which workload, and when. Once inside the container, the user has full access to the container's filesystem and processes. This is by design.

### Notebook Provider Integration (ADR-020)

The `get_connection_command(session_id)` method on the notebook provider interface (defined in ADR-020) is the backend for this feature. Each adapter implementation generates the appropriate command for its compute backend.

### UI Placement

The "Connect" button appears in three locations:

1. **Pipeline Run detail page:** Connect to the pod running the active pipeline job. Only visible while the job is running.
2. **Notebook Session detail page (in session management):** Connect to the pod running the Jupyter or RStudio session. Visible while the session is active.
3. **Infrastructure → Components → Compute:** For admin users, list all running pods/nodes with connect buttons. This provides cluster-wide access for debugging.

The button expands inline to show the command with a "Copy" button. First-time users also see a collapsible setup guide (kubeconfig configuration for K8s, SSH key setup for SLURM).

### Future: Web-Based IDE

A web-based IDE (e.g., code-server running as a sidecar container) is a natural evolution of this feature. It would provide:

- In-browser terminal access without local kubectl/SSH setup
- File browsing and editing within the container
- Full audit trail of file modifications (not just session initiation)

This is deferred to a future phase. The SSH/exec approach provides the critical capability now while the web IDE is designed and built.

---

## Consequences

**Positive:**

- Meets users where they are: teams that author pipelines interactively can do so immediately
- Minimal implementation effort: generating a command string from pod metadata is straightforward
- Audit logging provides accountability without restricting flexibility
- The setup guide reduces friction for users unfamiliar with kubectl

**Negative:**

- No visibility into what users do inside connected sessions: file modifications, package installs, and configuration changes are invisible to the platform
- Users can make changes inside containers that diverge from the GitOps-managed environment, creating drift
- Requires users to install and configure kubectl (or SSH client) locally: not zero-setup
- Security exposure: users with container access can potentially access environment variables, mounted secrets, or other containers in the namespace

**Mitigations:**

- Pipeline containers run with minimal RBAC permissions: they can access their own data volumes but not the Kubernetes API or other namespaces
- Secrets mounted into pipeline containers are limited to what that specific pipeline needs
- The platform displays a subtle reminder when the Connect button is used: "Changes made inside this session are not tracked by bioAF's environment management. Consider committing pipeline changes to your Git repository."

**Neutral:**

- This feature does not replace the existing pipeline import-from-GitHub workflow: both coexist
- The audit log entry for session initiation is consistent with the existing audit log schema (ADR-009)

---

## Amendment (2026-11-16): what shipped

The original implementation passed an already-prefixed notebook ID to the adapter (producing a nonexistent `bioaf-notebook-notebook-{id}`), reported the `bioaf-interactive` namespace although notebook pods run in `bioaf-notebooks`, guessed pipeline Job names, and the Connect control had no page consumers. The repair:

- **UI:** a **Terminal instructions** section with **Show instructions** on the pipeline run details page (catalog and custom runs, while running) and in Jupyter/RStudio session details (running or idle). It explains that no terminal is opened, shows the command, target workload and namespace, warning, and setup guide, and is discarded when the target changes or stops.
- **Eligibility:** `notebooks:launch` (effective permission; API-key scopes cannot carry it), the `ssh_exec` capability, an Organization-scoped record, a supported type and state. Work Node (`ssh`) and headless sessions are excluded.
- **Target identity:** taken from the record (`compute_job_ref`, then legacy fields), never from the request or a guess, and verified live with bounded metadata reads: the pipeline Job's run label and its owner-UID head pod, or the notebook pod's session/user/type labels, with the main container (`pipeline` or `notebook`) running. Conflicting or missing identity is refused.
- **Command:** `kubectl exec -it --context <context> -n <namespace> <pod> --container <container> -- /bin/sh`. The setup guide is selected by provider (GKE `get-credentials` with the real cluster and location; EKS `update-kubeconfig` with an explicit alias) and agrees with the context in the command.
- **Audit:** `connection_command_generated` names the record, Job/Pod, namespace, container, and context, and is committed before instructions are returned. It records that instructions were generated, not a session initiation.
- **Races:** the record is re-read before auditing; a stop or reference change refuses the request. A command remains a snapshot: if the workload ends later, the user refreshes and requests new instructions.
- **Administrator inventory:** `GET /api/infrastructure/compute/pods` lists only the Organization's eligible records from the database, each with a `record_id` for the per-record endpoint. The infrastructure UI described under UI Placement (item 3) is deferred, and no installation-wide authority is granted.
- **Deferred:** the administrator inventory page, headless execution access, Nextflow task-pod access, SLURM, and a browser terminal.

The user guide is [SSH and Exec Access](../docs/guides/ssh-access.md).

---

## References

- ADR-020 (BioAF Adapter Layer: `get_connection_command` on notebook provider interface)
- ADR-021 (Kubernetes compute backend: kubectl exec for K8s pods)
- ADR-009 (immutable audit log: session initiation logging)
