# SSH and Exec Access

Use Kubernetes exec to open a shell in a running Pipeline Run or a Jupyter/RStudio Notebook Session, and
SSH to connect to a Work Node. Work Nodes display SSH details on their page. Kubernetes exec and VM SSH have
different access requirements.

## Terminal Instructions

bioAF does not open a terminal for you. It gives you a command to run on your own computer, a first-time
setup guide for your cluster, and a reminder of what is and is not tracked.

Where to find them:

- **Pipeline run details:** the **Terminal instructions** section while the run is running. Catalog and
  custom runs are both supported.
- **Notebook session details:** the **Terminal instructions** section of a Jupyter or RStudio session that
  is running or idle.

Select **Show instructions** to prepare them. bioAF first checks, against the cluster, that the workload
recorded for this run or session still exists, carries this record's identity, and has its main container
running. Then it shows the command, the workload and namespace it targets, and the setup guide.

Who sees them: users whose role grants `notebooks:launch` (custom roles included; the built-in role name
does not matter), on an installation whose compute backend supports exec (`ssh_exec`). API keys cannot
request terminal instructions: `notebooks:launch` is not an API-key scope.

Supported targets:

| Workload | Eligible states | Container | Shell |
| --- | --- | --- | --- |
| Pipeline run (catalog or custom) | running | The run's head pod, container `pipeline` | `/bin/sh` |
| Jupyter or RStudio session | running, idle | The session's pod, container `notebook` | `/bin/sh` |

Nextflow task pods, headless notebook executions, and Work Nodes are not terminal targets here. Custom
notebook and pipeline images must provide `/bin/sh`.

### Prerequisites

You need, on your own computer:

- `kubectl`.
- Your cloud provider's CLI, signed in with your own account: the Google Cloud CLI with
  `gke-gcloud-auth-plugin` on GKE, or the AWS CLI on EKS.
- Kubernetes access to the cluster granted to your own identity. A bioAF permission does not grant
  Kubernetes RBAC, and bioAF never shares its own cluster credentials.

The setup guide names this installation's actual cluster and location, so there is nothing to fill in. On
GKE it runs `gcloud container clusters get-credentials` with the cluster's region (or zone). On EKS it runs
`aws eks update-kubeconfig` with an explicit `--alias`. The command always passes that `--context`, an
explicit namespace, and an explicit `--container`, so it does not depend on your current kubectl context.
A command has this form:

```bash
kubectl exec -it --context <context> -n <namespace> <pod> --container <container> -- /bin/sh
```

### When instructions are refused

- **The analysis is no longer running** (or it changed while instructions were prepared): refresh its
  details, then request new instructions.
- **bioAF could not check it with the cluster right now:** try again in a moment.
- **The cluster refused bioAF's check,** or **terminal access is not set up for this installation:** ask
  an administrator. These are about bioAF's own cluster access and configuration, not your permissions.
- Installations running in simulated compute mode have no cluster and never return a command.

Instructions describe the workload when they were prepared. If it ends later, the copied command fails;
refresh the details and request new instructions.

### API

The internal endpoints are `POST /api/pipeline-runs/{run_id}/connect` and
`POST /api/sessions/{session_id}/connect`. Both require `notebooks:launch`, the `ssh_exec` capability,
and a record in your Organization. Refusals use stable codes with a `details.reason`:
`connection_target_invalid` (409), `connection_unavailable` (409), `connection_provider_unavailable`
(502, reason `provider_denied` or `provider_unavailable`), and `connection_provider_timeout` (504).

Administrators with `infrastructure:configure` can list their Organization's eligible targets with
`GET /api/infrastructure/compute/pods`. It is a database snapshot: each entry carries the `record_id` to
pass to the per-record endpoint, which performs the live check. There is no administrator page for this
list yet.

## SSH Work Nodes

Work Nodes are separate virtual machines: GCE on the reference deployment, with an EC2 implementation on
AWS. Open **Workbench > Work Nodes**, launch with a ready environment version and supported scope, and use
the host/credential details shown after provisioning. Project scope alone does not expand file mounts.

Viewing Work Nodes requires `work_nodes:view`. Launching and stopping them require
`work_nodes:launch` and `work_nodes:stop`, respectively. Use the credentials and SSH instructions for
the selected node.

## Persistence and Auditing

Preserve outputs through the workload's documented storage and registration flow. Container edits and VM
scratch are not substitutes for registered output storage. See [Custom Pipelines](custom-pipelines.md#the-runtime-contract).

Each successful request for terminal instructions writes a `connection_command_generated` audit entry
naming the run or session, the Job or Pod, the namespace, container, and cluster context. That records
that instructions were shown, not that anyone connected: bioAF cannot see whether the command was run,
and it does not record commands executed in a shell. Direct operator exec creates no bioAF audit entry.
Editing files or configuration inside a running workload can change that analysis. There is no
bioAF-managed SSH transcript in this workflow.

The host-side `./bioaf` management script is separate. Its help provides an operator summary; commands such
as `bioaf compute ssh` and `bioaf compute list` are not supported by that script.
