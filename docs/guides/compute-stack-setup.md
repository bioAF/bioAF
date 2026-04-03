# Compute Stack Setup

bioAF supports two compute backends through the BioAF Adapter Layer (BAL): Kubernetes (GKE Autopilot) and SLURM (via Google HPC Toolkit). This guide helps you choose the right backend for your team and walks through the setup process for each option.

## Kubernetes vs SLURM: When to Use Each

Kubernetes (GKE Autopilot) is the recommended default for most teams. SLURM is available for teams with existing SLURM expertise or specific HPC requirements.

**Choose Kubernetes if:**

- You are starting fresh without existing infrastructure preferences.
- Cost efficiency matters -- GKE Autopilot charges only for running pods, with no idle controller or login node costs.
- You want managed autoscaling, self-healing, and native GCP integration out of the box.
- Your pipelines are containerized (Nextflow, Snakemake with container profiles).
- You plan to use bioAF's automated pipeline triggering, which integrates natively with Kubernetes job scheduling.

**Choose SLURM if:**

- Your team has deep SLURM expertise and existing job scripts written for SLURM.
- You need POSIX filesystem semantics for pipeline working directories (NFS Filestore).
- Your pipelines depend on environment modules or SLURM-specific features (job arrays, accounting).
- You are migrating from an on-premises SLURM cluster and want minimal workflow disruption.

## Cost Comparison

| Component | Kubernetes (GKE Autopilot) | SLURM (HPC Toolkit) |
|-----------|---------------------------|----------------------|
| Idle cost | ~$0/month (no persistent nodes) | ~$50-80/month (controller + login node) |
| Storage | GCS at ~$0.02/GB/month | Filestore minimum 1TB at ~$200/month (HDD) |
| Compute | Pay-per-pod-second | Pay-per-VM-hour (preemptible available) |
| Autoscaling | Native, sub-minute | VM-based, 2-5 minute spin-up |
| Management | Fully managed by Google | Self-managed VMs via Terraform |

For a typical small biotech team processing 5-10 experiments per month, Kubernetes with GCS costs roughly $50-150/month in compute. The equivalent SLURM setup starts at $250-350/month before any pipeline runs due to the Filestore minimum and always-on nodes.

## Setting Up Kubernetes (Recommended)

### Step 1: Enable the Compute Component

Navigate to **Settings > Infrastructure > Optional Components** in the bioAF UI. Toggle on "Kubernetes Compute." bioAF automatically provisions:

- A GKE Autopilot cluster in your selected region.
- A dedicated node pool namespace for pipeline workloads.
- IAM bindings for the bioAF service account to submit jobs.
- Network policies restricting pod-to-pod communication.

Provisioning takes 5-10 minutes. You can track progress in the deployment modal.

### Step 3: Verify Connectivity

Once provisioning completes, navigate to **Compute > Status**. You should see a green health check for the Kubernetes backend. Run a test pipeline (bioAF includes a built-in "hello-world" pipeline) to confirm end-to-end functionality.

### Step 4: Configure Resource Defaults

Under **Compute > Settings**, set default resource requests for pipeline jobs:

- **CPU request:** 2 cores (sufficient for most alignment steps)
- **Memory request:** 8Gi (Cell Ranger recommends 8GB minimum)
- **CPU limit:** 8 cores (allows bursting for multi-threaded steps)
- **Memory limit:** 32Gi (prevents OOM kills during peak usage)

These defaults apply to all pipeline runs unless overridden at launch time.

## Setting Up SLURM

### Step 1: Enable the Compute Component

Navigate to **Settings > Infrastructure > Optional Components**. Toggle on "SLURM Compute." bioAF automatically provisions:

- A SLURM controller VM (e2-standard-2).
- A login node VM (e2-standard-2).
- A Filestore NFS instance (1TB minimum).
- Compute node templates with autoscaling configuration.

Provisioning takes 10-15 minutes due to the Filestore and SLURM controller setup. Note the Filestore cost -- this is a fixed monthly expense regardless of usage.

### Step 3: Configure Partitions

After provisioning, navigate to **Compute > SLURM > Partitions**. bioAF creates a default partition with autoscaling compute nodes. You may want to add partitions for different workload types:

- **short** -- max 4 hours, preemptible VMs, for QC and small alignment jobs.
- **long** -- max 72 hours, standard VMs, for full Cell Ranger runs.
- **highmem** -- high-memory VMs (n1-highmem-16), for integration analyses.

### Step 4: Verify with a Test Job

Submit a test pipeline from the bioAF UI. Monitor the SLURM queue in **Compute > SLURM > Queue** to verify that nodes spin up, the job runs, and nodes scale back down after the idle timeout.

## Switching Between Backends

bioAF supports running both backends simultaneously. You can assign pipelines to specific backends in the pipeline configuration. To switch the default backend, navigate to **Compute > Settings > Default Backend** and select your preferred option. Existing pipeline runs continue on their original backend; only new runs use the updated default.

## Tips

- Start with Kubernetes unless you have a specific reason to use SLURM. You can always add SLURM later.
- Set budget alerts in **Settings > Cost Management** before running large batch jobs. bioAF's budget pre-flight check will warn you before launching jobs that exceed your configured threshold.
- For Cell Ranger alignment, request at least 8 cores and 32GB memory. Under-provisioning leads to silent slowdowns rather than clear error messages.
- Use preemptible/spot VMs for fault-tolerant pipelines (Nextflow handles retries natively). This can reduce compute costs by 60-80%.
