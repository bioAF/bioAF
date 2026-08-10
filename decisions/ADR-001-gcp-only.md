# ADR-001: GCP as Sole Supported Cloud Provider

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF needs to provision and manage significant cloud infrastructure: managed Kubernetes, managed PostgreSQL, object storage, HPC compute nodes, managed file systems, secrets management, and networking. Supporting multiple cloud providers would multiply the Terraform modules, testing surface, and operational complexity.

Our target users are small biotech startups (5–30 people) with no infrastructure team. They need one path that works reliably, not three paths that work partially.

## Decision

bioAF will support Google Cloud Platform (GCP) only. AWS and Azure support are explicitly out of scope for the core project. Community contributors are welcome to build provider support for other clouds.

## Rationale

- **Google HPC Toolkit** provides production-grade Terraform modules for SLURM on GCP that would need to be rebuilt from scratch for AWS (ParallelCluster) or Azure (CycleCloud).
- **GKE Autopilot** provides managed Kubernetes with minimal configuration. EKS and AKS have different operational models.
- **Cloud SQL** provides managed PostgreSQL with point-in-time recovery, automated backups, and IAM integration. RDS and Azure SQL have equivalent features but different Terraform resource models and configuration patterns.
- **Google Secret Manager** integrates natively with GKE workload identity. AWS Secrets Manager and Azure Key Vault have different auth models.
- Maintaining parity across three clouds would at least triple the infrastructure code and testing burden, which is untenable for a small open-source project.
- GCP has strong positioning in the life sciences space (Terra, Google Cloud Life Sciences, Verily partnerships), so our target users are likely to already have or be willing to adopt GCP.

## Consequences

- bioAF is inaccessible to teams committed to AWS or Azure unless community support is built.
- All Terraform modules, CLI tooling, and documentation assume GCP.
- Where practical, GCP-specific abstractions should be isolated (e.g., storage access through a thin wrapper, not direct GCS SDK calls everywhere) to reduce the eventual cost of multi-cloud support if it becomes a priority. However, this is a soft goal, not a constraint: we will not over-abstract at the expense of simplicity or development speed.
- The architecture spec should document which GCP services are used and what their equivalents would be on other providers, to help potential community contributors.
