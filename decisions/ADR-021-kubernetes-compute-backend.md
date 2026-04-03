# ADR-021: Kubernetes as Recommended Compute Backend

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

bioAF's original architecture specified SLURM as the compute orchestrator, reflecting its status as the de facto standard in bioinformatics HPC. However, feedback from practicing computational biologists — all of whom had used SLURM — was unanimous: starting fresh, they would choose Kubernetes. Their reasoning:

1. **Cost:** SLURM requires a dedicated controller node and login node that run continuously (~$50-80/month idle). Kubernetes on GKE Autopilot only charges for resources when pods are running.
2. **Cloud-native:** GKE is a managed service with native autoscaling, self-healing, and integrated monitoring. SLURM on GCP requires managing VMs manually.
3. **Portability:** Kubernetes runs on GCP, AWS, and Azure. SLURM on cloud is GCP-specific in the bioAF context (Google HPC Toolkit).
4. **Ecosystem:** The Kubernetes ecosystem (Helm charts, operators, observability tools) is vastly larger than SLURM's cloud tooling.

bioAF already runs its control plane on GKE. Running pipeline workloads on the same cluster (or a dedicated workload node pool) eliminates an entire infrastructure layer.

---

## Decision

Implement Kubernetes as the recommended and default compute backend for bioAF, using the BAL compute provider interface defined in ADR-020. SLURM will be listed as an alternative option in the setup wizard but stubbed as "coming soon" until a future phase delivers the SLURM adapter.

### Architecture

**Pipeline jobs run as Kubernetes Jobs.** When a pipeline run is submitted:

1. The pipeline launcher creates a `pipeline_runs` record in Postgres
2. The K8s compute adapter constructs a Kubernetes Job manifest specifying the pipeline container image, resource requests/limits, input/output volume mounts (GCS via sidecar or init container), and environment variables
3. The Job is submitted to the workload node pool via the Kubernetes API
4. GKE Autopilot autoscales nodes as needed to schedule the Job's pods
5. The adapter polls Job status and streams logs back to the monitoring UI
6. On completion, the adapter triggers output collection and experiment status updates

**Nextflow integration.** Nextflow natively supports a `k8s` executor. bioAF auto-generates a Nextflow config:

```groovy
// bioaf-nextflow.config (auto-generated for K8s stack)
profiles {
    bioaf_k8s {
        process.executor = 'k8s'
        process.container = 'nfcore/scrnaseq:latest'
        k8s.namespace = 'bioaf-pipelines'
        k8s.serviceAccount = 'bioaf-pipeline-runner'
        k8s.storageClaimName = 'bioaf-working-pvc'  // if using persistent volume
        // OR for GCS-native:
        process.publishDir = 'gs://bioaf-results-{org}/'
        params.outdir = 'gs://bioaf-results-{org}/'
    }
}
```

**Snakemake integration.** Snakemake supports Kubernetes execution via its `--kubernetes` flag. bioAF generates a Snakemake profile with equivalent settings.

### Node Pool Configuration

For GKE Autopilot, node provisioning is fully managed — bioAF simply specifies resource requests on pods and Autopilot handles node selection. For GKE Standard (if users need more control), bioAF configures:

| Node Pool | Purpose | Machine Type | Autoscaling | Spot/On-Demand |
|---|---|---|---|---|
| `bioaf-platform` | Control plane services | e2-standard-2 | 1-3 nodes | On-demand |
| `bioaf-pipelines` | Pipeline batch jobs | n2-highmem-16 | 0-20 nodes | Spot (default, configurable) |
| `bioaf-interactive` | Notebook sessions | n2-standard-4 | 0-5 nodes | On-demand |

Node pool configuration (machine types, max nodes, spot toggle) is managed through the bioAF UI, which updates `terraform.tfvars` and applies via the existing Terraform executor (ADR-007).

### Cost Tracking

- GKE usage is tracked via GCP Billing API, broken down by node pool and namespace
- Per-pipeline-run cost is estimated from pod resource consumption multiplied by machine type pricing
- Historical cost data feeds into the predictive cost estimator used by the automated pipeline trigger system (ADR-025)
- Spot vs. on-demand cost comparison is surfaced in the Cost Center

### Container Strategy

Pipeline tools run inside Docker containers (not Singularity as in the SLURM architecture). This is the native container format for Kubernetes. nf-core pipelines already publish Docker images. Custom pipelines can specify any Docker image.

The `bioaf-scrna` base environment (scanpy, anndata, scvi-tools, Seurat, Bioconductor, etc.) is built as a Docker image and published to the project's Artifact Registry. This image serves as the default for notebooks and as a base for custom pipeline containers.

### Terraform Modules

```hcl
# compute-k8s.tf
resource "google_container_node_pool" "pipelines" {
  count    = var.compute_stack == "kubernetes" ? 1 : 0
  name     = "bioaf-pipelines"
  cluster  = google_container_cluster.bioaf.id

  autoscaling {
    min_node_count = 0
    max_node_count = var.k8s_pipeline_max_nodes
  }

  node_config {
    machine_type = var.k8s_pipeline_machine_type
    spot         = var.k8s_pipeline_use_spot

    labels = {
      "bioaf.io/pool" = "pipelines"
    }

    taint {
      key    = "bioaf.io/pool"
      value  = "pipelines"
      effect = "NO_SCHEDULE"
    }
  }
}
```

Pipeline pods include a node selector and toleration to target the `bioaf-pipelines` node pool. Interactive session pods target `bioaf-interactive`.

### Migration Path from SLURM

For teams that initially chose SLURM and later want to migrate to Kubernetes:

- This is not supported as an in-place migration in v1
- Teams would deploy a new bioAF instance with K8s, export experiment data from the old instance, and import into the new one
- A structured migration guide will be provided in documentation

---

## Consequences

**Positive:**

- Eliminates SLURM controller and login node costs (~$50-80/month)
- Single orchestrator (GKE) for both platform services and workloads
- Native autoscaling to zero — no idle compute charges
- Docker containers are the standard; no Singularity conversion needed
- Pipeline monitoring integrates with standard K8s tooling (kubectl, Prometheus, Grafana)
- Path to multi-cloud: K8s runs on AWS (EKS) and Azure (AKS) with minimal adapter changes

**Negative:**

- Some nf-core pipelines are tested primarily on SLURM; K8s executor may have edge cases
- Teams accustomed to SLURM job scripting (sbatch, srun) need to adapt
- K8s resource requests/limits are less intuitive than SLURM's partition model for some users
- Autopilot pricing can be less predictable than reserved SLURM nodes for sustained workloads

**Neutral:**

- Nextflow and Snakemake both support K8s executors natively; pipeline definitions do not change
- The BAL abstraction (ADR-020) means the UI and service layer are unaffected by the backend choice

---

## References

- ADR-020 (BioAF Adapter Layer)
- ADR-007 (UI-driven Terraform)
- ADR-022 (GCS storage adapter)
