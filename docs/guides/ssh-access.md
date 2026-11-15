# SSH and Exec Access

Use Kubernetes exec to open a shell in a Pipeline Run or Notebook Session, and SSH to connect to a Work
Node. Work Nodes display SSH details on their page. Connection-command endpoints provide Kubernetes
commands and setup instructions. Kubernetes exec and VM SSH have different access requirements.

## Pipeline Jobs

For a running run, the internal API exposes `POST /api/pipeline-runs/{run_id}/connect`. It requires
`notebooks:launch`, the `ssh_exec` provider capability and Organization ownership. The response includes a
command and setup guide. You still need cloud/Kubernetes credentials on your local machine.

A pipeline exec command has this form after substituting the actual job reference:

```bash
JOB_REF="replace-with-the-running-job-reference"
kubectl exec -it -n bioaf-pipelines "job/$JOB_REF" -- /bin/bash
```

A bioAF permission does not grant Kubernetes RBAC or install local cluster credentials.

## Notebook Sessions

For a running or idle Notebook Session, use `POST /api/sessions/{session_id}/connect` to retrieve a
connection command and setup guide. This requires `notebooks:launch`, the `ssh_exec` provider capability
and Organization ownership. Notebook pods run in the `bioaf-notebooks` namespace.

To connect directly with configured cluster credentials, find the session's pod and open a shell:

```bash
kubectl get pods -n bioaf-notebooks
POD_NAME="replace-with-the-actual-running-pod-name"
kubectl exec -it -n bioaf-notebooks "pod/$POD_NAME" -- /bin/bash
```

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

The pipeline/notebook endpoints audit generation of a connection command. They do not record commands
subsequently executed in a shell, and direct operator exec does not create those API audit entries.
There is no bioAF-managed SSH transcript switch in this workflow.

The host-side `./bioaf` management script is separate. Its help provides an operator summary; commands such
as `bioaf compute ssh` and `bioaf compute list` are not supported by that script.
