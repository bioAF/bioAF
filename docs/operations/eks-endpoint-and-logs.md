# EKS API endpoint and control-plane logs (AWS installations)

bioAF manages who can reach an AWS installation's Kubernetes API and keeps an audit trail of the
control plane. This page covers what changes on upgrade, how an administrator moves an existing
cluster to a restricted endpoint, how operators read the logs, and how to recover if the cutover
goes wrong.

EKS still authenticates and authorizes every request (IAM access entries and Kubernetes RBAC).
These settings only decide which networks can open a connection.

## Endpoint modes

| Mode | Public endpoint | Private endpoint | Used for |
| --- | --- | --- | --- |
| `private_only` | Off | On | Default for a new cluster. The backend reaches the API over the VPC peering it already uses for Work Node sync, on tcp/443 from the application's security group. |
| `public_restricted` | On, only from approved CIDRs | On | An installation that opts in, with the application's stable public egress (if it uses the public endpoint) and approved operator egress. `0.0.0.0/0` and `::/0` are refused. |
| `preserve_existing` | As it was | As it was | An existing cluster after upgrade, until an administrator runs a reviewed cutover. |

A new cluster in `private_only` needs the application network recorded (`aws_app_vpc_id` and
`aws_app_security_group_id`). Without it the compute deploy stops with an error; it never falls
back to public access.

## What happens on upgrade

The first compute plan after the upgrade (a compute deploy or **Infrastructure > Components >
Check for Infrastructure Updates**) records the existing cluster:

1. It reads the cluster from the remote compute state without refreshing it, reads the live
   cluster with `DescribeCluster`, and checks they are the same cluster (ARN, creation time,
   account, region, name).
2. It records the live endpoint settings as `preserve_existing`. Nothing about the endpoint
   changes. If the check fails, the compute plan stops with an explanation and nothing is recorded.

Every later compute plan passes those exact settings back and refuses any endpoint change. Before
each apply the live cluster is checked again: it must be the recorded cluster (same ARN and creation
time, in AWS and in the remote state) with the recorded endpoint. If anything changed outside
bioAF, the apply stops until it is reviewed.

A compute apply is the reviewed plan. It refuses inputs that changed since the plan, plans again,
refuses any change whose actions or values differ from the reviewed plan (and any cluster
replacement), and applies that checked plan file.

The same upgrade turns on control-plane logging (all five types) with 30-day retention. Logging
does not need the cutover.

An unrestricted preserved endpoint still counts as **pending hardening**:

```sh
curl -s -H "Authorization: Bearer ADMIN_TOKEN" https://BIOAF_HOST/api/v1/infrastructure/eks-endpoint
```

`pending_hardening: true` means the cluster still has its preserved settings.

## Before a cutover

Run these yourself and keep the output with the change record.

1. **Record the current endpoint.**

   ```sh
   aws eks describe-cluster --name CLUSTER --region REGION \
     --query 'cluster.resourcesVpcConfig' > endpoint-before.json
   ```

2. **Prepare an independent recovery path.** Use an operator IAM principal that is not `bioaf-app`
   and has `eks:DescribeCluster` and `eks:UpdateClusterConfig` on this cluster. Confirm it works
   from your own machine while bioAF is running:

   ```sh
   aws eks describe-cluster --name CLUSTER --region REGION --query 'cluster.status'
   ```

   For Kubernetes access after a private-only cutover, give that principal its own EKS access
   entry and connect from the app VM or another network peered with the cluster VPC. Network
   access alone does not grant Kubernetes authorization.

3. **For `private_only`: check the private path from the app VM.** The compute apply after the
   upgrade creates the peering, the routes and the tcp/443 rule. Find the control plane's private
   addresses and connect to one from the app VM:

   ```sh
   aws ec2 describe-network-interfaces --region REGION \
     --filters "Name=description,Values=Amazon EKS CLUSTER" \
     --query 'NetworkInterfaces[].PrivateIpAddress'
   # on the app VM:
   curl -sk --max-time 5 https://PRIVATE_IP/healthz; echo
   ```

   Any HTTP response (including `401` or `403`) proves the route and security group. A timeout
   means the peering, routes or tcp/443 rule are missing; do not cut over.

   After the cutover, the endpoint's public DNS name resolves to these private addresses (AWS
   behavior for a private-only endpoint), so the backend needs no DNS change.

4. **For `public_restricted`: collect the CIDRs.** Use the app VM's stable public address (its
   Elastic IP) if the backend will use the public endpoint, plus each approved operator range.

5. **Check subnet capacity.** EKS needs free addresses in the cluster subnets to update its
   control-plane network interfaces.

## Run the cutover

1. Schedule it:

   ```sh
   curl -s -X POST -H "Authorization: Bearer ADMIN_TOKEN" -H "Content-Type: application/json" \
     https://BIOAF_HOST/api/v1/infrastructure/eks-endpoint/cutover \
     -d '{"mode": "private_only", "recovery_paths_verified": true}'
   ```

   For restricted public access use
   `{"mode": "public_restricted", "public_access_cidrs": ["203.0.113.10/32"], "recovery_paths_verified": true}`.
   bioAF refuses the request if the live endpoint no longer matches the recorded settings.

2. Open **Infrastructure > Components** and run **Check for Infrastructure Updates**. Review the
   cluster's endpoint change in the plan, then confirm it.

3. After the apply, bioAF reads the cluster back. The new mode is recorded only if the live
   endpoint matches the target. Check:

   ```sh
   curl -s -H "Authorization: Bearer ADMIN_TOKEN" https://BIOAF_HOST/api/v1/infrastructure/eks-endpoint
   ```

   Then open **Workbench > Notebook Sessions** or **Pipelines > Pipeline Runs** and confirm the
   cluster responds, and confirm nodes are `Ready` from your operator access.

To drop a scheduled cutover before it runs:

```sh
curl -s -X DELETE -H "Authorization: Bearer ADMIN_TOKEN" \
  https://BIOAF_HOST/api/v1/infrastructure/eks-endpoint/cutover
```

## If the cutover fails

A failed or interrupted apply leaves the cutover marked `needs_reconcile` or `applying`. The next
compute plan reads the live endpoint:

- It matches the target: the new mode is recorded.
- It still matches the old settings: the cutover stays scheduled and can be planned again.
- It matches neither: the plan stops and nothing changes. Use the recovery path below.

bioAF never reopens the endpoint to the internet on its own. To restore access while bioAF cannot
reach the cluster, use your operator principal:

```sh
aws eks update-cluster-config --name CLUSTER --region REGION \
  --resources-vpc-config endpointPublicAccess=true,publicAccessCidrs="OPERATOR_IP/32",endpointPrivateAccess=true
aws eks wait cluster-active --name CLUSTER --region REGION
```

Restore the values from `endpoint-before.json` only after review. Then run a compute plan so bioAF
reconciles what is live.

## Control-plane logs

- **Where:** CloudWatch log group `/aws/eks/CLUSTER/cluster`, with all five log types (`api`,
  `audit`, `authenticator`, `controllerManager`, `scheduler`).
- **Retention:** 30 days by default. Change it with
  `PUT /api/v1/infrastructure/eks-endpoint/settings` and `{"log_retention_days": 90}` (a CloudWatch
  Logs retention value), then run **Check for Infrastructure Updates**. A log-type override must
  keep `audit` and `authenticator`.
- **Cost:** CloudWatch charges for ingestion and storage go to the installation's AWS account.
  Retention limits storage time, not ingestion.
- **Who can read:** bioAF grants no control-plane log reads to the app or to scientists. An operator
  creates this policy with their own credentials and attaches it to the designated infrastructure
  or security roles:

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["logs:DescribeLogStreams", "logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery", "logs:GetLogGroupFields"],
        "Resource": [
          "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/eks/CLUSTER/cluster",
          "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/eks/CLUSTER/cluster:*"
        ]
      },
      {
        "Effect": "Allow",
        "Action": ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StopQuery"],
        "Resource": "*"
      }
    ]
  }
  ```

  The same document is the compute module's `control_plane_log_reader_policy_json` output.
- **Teardown:** the log group and its history are kept when compute is torn down. A later deploy
  with the same cluster name adopts the group instead of failing. Delete it yourself when it is no
  longer needed.
