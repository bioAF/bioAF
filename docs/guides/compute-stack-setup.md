# Compute Stack Setup

Kubernetes is the implemented compute backend. Pipelines and Notebook Sessions run in the cluster;
SSH Work Nodes run on separate virtual machines. SLURM compute and notebook adapters are stubs and cannot
run workloads, even though the legacy configuration value is accepted.

## Cloud and Backend Choices

| Resource | GCP reference deployment | AWS implementation |
| --- | --- | --- |
| Kubernetes cluster | GKE with separate managed node pools | EKS with Cluster Autoscaler |
| Notebook Sessions | Jupyter or RStudio pods | Kubernetes notebook implementation with AWS identity/storage paths |
| Work Nodes | GCE virtual machines | EC2 virtual machines |
| Object storage | GCS | S3 |
| Environment images | Cloud Build and Artifact Registry | CodeBuild and ECR |

AWS parity is incomplete, especially for cellxgene and several platform-service adapters. Validate the
specific workflow before relying on it. NFS is an implemented storage backend, not a working SLURM stack.
The GCP Terraform module creates node pools; it is not an Autopilot-only deployment.

## Provision Infrastructure

1. Complete first-run setup and configure cloud access in **Settings > Integrations**.
2. Open **Infrastructure > Components** and use its deployment controls to provision the compute stack.
3. Follow the progress display through to a confirmed outcome. A closed connection or progress message is
   not sufficient evidence that Terraform succeeded.
4. Enable the notebook, pipeline and visualization components you need. Components may wait for their
   infrastructure or image-build prerequisites.
5. Check component health, then launch a small test workload appropriate to the selected backend.

Use the deployment page's recovery controls if provisioning is interrupted. A deploy that reaches the
cloud but loses its response can leave resources to reconcile. Recovery actions require
`infrastructure:deploy`. See the [Admin Guide](../user-guide-admin.md#deployment-recovery).

## Configure Workloads

- Select a ready environment version and a resource profile for Notebook Sessions.
- Configure pipeline parameters and resources from **Pipelines > Pipeline Catalog** or **Custom Pipelines**.
- Configure Work Node defaults under **Settings > Workbench Settings** and launch from **Workbench > Work Nodes**.
- Review quota and capacity feedback before increasing resource requests. Cloud pricing and fixed costs depend
  on the actual cluster, node pools, disks, networking and region; use the cloud estimate and Cost Center.

Each launch is subject to permissions, Organization ownership and provider capabilities. Project scope on
an interactive session is recorded and preserved by Recreate; it does not grant access to extra files.

## Local Simulation

`BIOAF_COMPUTE_MODE=local` simulates the mode-aware compute, notebook, storage and Work Node adapters.
`kubernetes` (or `k8s`) selects real execution. `cloud_provider` and the configured compute stack select the
actual providers independently of this mode.

Local mode is not a complete offline sandbox: cellxgene has no simulation and other external integrations
can still make requests. Do not treat simulated success as evidence of a real cloud launch.
See [Configuration](../configuration.md) and [Development](../development.md).

## Connecting to Workloads

Work Nodes display SSH details on their page. Pipeline and Notebook Session connection-command endpoints
provide Kubernetes exec commands and setup instructions. See [SSH and Exec Access](ssh-access.md) for
connection methods, credentials and permissions.
