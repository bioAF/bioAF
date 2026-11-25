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
running. Then it shows the command and the setup guide. **Connection details** (collapsed) lists the exact
workload, pod, container, namespace, and cluster context, each with a short definition, for diagnosis.

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
GKE it runs `gcloud container clusters get-credentials` with the region (or zone) the cluster was deployed
in, which can differ from the installation's default region. On EKS it runs `aws eks update-kubeconfig`
with an explicit `--alias` that includes the AWS account, region, and cluster name, so a copied command
never selects a same-named cluster in another account. The command always passes that `--context`, an
explicit namespace, and an explicit `--container`, so it does not depend on your current kubectl context.
A command has this form:

```bash
kubectl exec -it --context <context> -n <namespace> <pod> --container <container> -- /bin/sh
```

### When instructions are refused

- **The analysis is no longer running** (or it changed while instructions were prepared): refresh its
  details, then request new instructions.
- **The analysis has not started running in the cluster yet:** its workload exists but is still waiting for
  a node or its container image (a pool scaling up from zero takes a few minutes). Try again in a moment.
- **bioAF could not check it with the cluster right now:** try again in a moment.
- **The cluster refused bioAF's check,** or **terminal access is not set up for this installation:** ask
  an administrator. These are about bioAF's own cluster access and configuration, not your permissions.
  On a GKE installation whose cluster was deployed before bioAF recorded the cluster's location, bioAF
  finds it the first time it is needed: it lists the project's clusters across all locations, and if
  exactly one matches the recorded cluster name (and Google Cloud reports the list as complete) it
  records that location. This needs bioAF's Google Cloud identity to be allowed to list clusters
  (`container.clusters.list`). If the cluster cannot be found that way, an administrator re-applies the
  compute stack, which records the location from the deployment itself.
- Installations running in simulated compute mode have no cluster, do not offer terminal instructions,
  and never return a command.

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

### Logging in with your key

Your bioAF Git key (profile > **Git SSH Key**) is also your login key for your own work nodes. Select
**Download key**, then on your computer:

```bash
mv ~/Downloads/bioaf_id_rsa ~/.ssh/bioaf_id_rsa
chmod 600 ~/.ssh/bioaf_id_rsa
ssh -i ~/.ssh/bioaf_id_rsa <username>@<work node address>
```

The same key also gives access to your GitHub repositories, so keep it private. Only you can download your
key; administrators cannot. If you lose the file, download it again: the key does not change. bioAF will not
replace your key while any of your work nodes may still need it (**Finish and stop your current work nodes
before replacing this key. You can download your current key again.**). A new key cannot log in to a work
node launched with the old one, and after replacing it you add the new public key to GitHub.

Password login keeps working unless an administrator sets a work node range (below): then password or key
works from inside the range, and only your key from anywhere else. If a range is in force and you have no
key yet, bioAF creates one for you at launch.

### When a stop cannot finish

Stopping a work node first saves `/outputs/` and your notebooks and scripts, then deletes the VM. If saving
does not succeed, bioAF keeps the VM and its disk and shows **Stop failed** with one of:

- **Your files could not be saved. The work node has been kept, so you can keep working and try again.**
  The node stays connectable and still counts toward your limit and costs.
- **Your files were saved, but bioAF could not finish stopping this work node.**
- **bioAF could not check this work node. Try again shortly.**

Use **Retry stop**. There is no option to discard the VM or its files. Idle cleanup retries a stop a few
times, then leaves the node for its owner.

### How bioAF trusts a work node

Each new work node gets its own server key, recorded before launch; bioAF only runs its save command on the
server it launched and never trusts a key on first use. The server key also appears in the VM's cloud
metadata (`bioaf-host-public-key` on Google Cloud), so you can check it with bioAF down. Anyone who can read
that VM's cloud metadata can read the key as well; treat cloud read access to work node VMs as sensitive.
New work nodes are saved over the private network (Google Cloud: a firewall rule allowing SSH only from the
bioAF server's internal address; AWS: VPC peering after the compute stack is re-applied).

Git cloning on work nodes, RStudio sessions and custom pipelines verifies GitHub with keys shipped in each
release and never scans for keys. A repository on another Git server needs that server's keys approved first
(see the [Admin Guide](../user-guide-admin.md#security-and-ssh-access)).

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
