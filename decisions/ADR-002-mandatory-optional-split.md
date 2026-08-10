# ADR-002: Mandatory Foundation with Optional Components

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF provisions a large number of infrastructure components: GKE, Cloud SQL, GCS, SLURM, Filestore, JupyterHub, RStudio, Nextflow, Snakemake, cellxgene, Meilisearch, and monitoring. Deploying everything at once increases the initial deployment time, cost, and complexity. Not every team needs every component immediately: some may want to start with experiment tracking and data management before enabling compute.

## Decision

Split components into a mandatory foundation (always deployed by `bioaf deploy`) and optional components (enabled individually via the UI, each triggering a Terraform apply).

**Mandatory:** GKE (Autopilot), Cloud SQL (PostgreSQL), GCS buckets, VPC + networking, Secret Manager, monitoring (Prometheus + Grafana), backup system, auth system, bioAF control plane.

**Optional:** SLURM cluster, Filestore, JupyterHub, RStudio Server, Nextflow, Snakemake, cellxgene, Meilisearch, QC Dashboard.

Optional components have declared dependencies. Enabling a component that depends on another triggers a prompt to enable the dependency first.

## Rationale

- **Faster initial deployment:** The mandatory foundation deploys in ~30 minutes. Adding SLURM and compute extends this by ~10–15 minutes per component, but users control when they pay that cost.
- **Lower floor cost:** The mandatory foundation idles at ~$110/month. Teams that only need experiment tracking and data management don't pay for compute they're not using.
- **Progressive disclosure:** Bench scientists and leadership can start using the experiment tracker immediately. Compute and pipeline features are added when the comp bio team is ready.
- **Clear cost attribution:** Each optional component has a declared cost estimate. Users understand what they're paying for and can make informed decisions.

## Consequences

- The bioAF UI must include a component catalog with enable/disable controls, dependency resolution, cost estimates, and Terraform plan review.
- The Terraform module structure must support selective component deployment. Each optional component is a separate `.tf` file with a feature flag variable.
- The UI must gracefully handle partial deployments: navigation items for disabled components show a clear "not enabled" state with an "Enable" action rather than being hidden (discoverability).
- The dependency graph must be enforced: SLURM → Filestore → JupyterHub/RStudio → Nextflow/Snakemake. Disabling SLURM cascades to all dependent components with a clear warning.
