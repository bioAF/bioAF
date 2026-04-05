# Architecture Decision Records

| ADR | Title | Status | Date |
| ----- | ------- | -------- | ------ |
| [ADR-001](ADR-001-gcp-only.md) | GCP as Sole Supported Cloud Provider | Accepted | 2026-03-05 |
| [ADR-002](ADR-002-mandatory-optional-split.md) | Mandatory Foundation with Optional Components | Accepted | 2026-03-05 |
| [ADR-003](ADR-003-email-based-auth.md) | Email-Based Authentication as Primary Auth Method | Accepted | 2026-03-05 |
| [ADR-004](ADR-004-tiered-backup-strategy.md) | Tiered Backup Strategy with Mandatory Database Backups | Accepted | 2026-03-05 |
| [ADR-005](ADR-005-github-based-upgrades.md) | GitHub-Based Versioning and Upgrade System | Accepted | 2026-03-05 |
| [ADR-006](ADR-006-experiment-tracking-as-foundation.md) | Experiment Tracking as Architectural Foundation | Accepted | 2026-03-05 |
| [ADR-007](ADR-007-ui-driven-terraform.md) | UI-Driven Terraform Execution for Infrastructure Management | Accepted | 2026-03-05 |
| [ADR-008](ADR-008-secret-manager.md) | Google Secret Manager for All Secrets | Accepted | 2026-03-05 |
| [ADR-009](ADR-009-immutable-audit-log.md) | Immutable Audit Log via Database-Level Enforcement | Accepted | 2026-03-05 |
| [ADR-010](ADR-010-notification-system.md) | Built-in Notification System with Email and Slack Connectors | Accepted | 2026-03-05 |
| [ADR-011](ADR-011-scrna-seq-initial-scope.md) | Single-Cell RNA-seq as Initial Workflow Scope | Accepted | 2026-03-05 |
| [ADR-012](ADR-012-data-portability.md) | Customer Owns All Data and Infrastructure State | Accepted | 2026-03-05 |
| [ADR-013](ADR-013-minseqe-compliant-metadata.md) | MINSEQE-Compliant Structured Metadata Schema | Accepted | 2026-03-06 |
| [ADR-014](ADR-014-geo-export-service.md) | GEO Export Service for Publication-Ready Data Submission | Accepted | 2026-03-06 |
| [ADR-015](ADR-015-analysis-snapshot-sdk.md) | Analysis Snapshot SDK for Iterative Analysis Provenance | Accepted | 2026-03-06 |
| [ADR-016](ADR-016-snapshot-comparison-ui.md) | Snapshot Comparison UI | Accepted | 2026-03-06 |
| [ADR-017](ADR-017-reference-data-management.md) | Managed Reference Data Layer | Accepted | 2026-03-06 |
| [ADR-018](ADR-018-cross-experiment-projects.md) | Cross-Experiment Analysis Projects | Accepted | 2026-03-06 |
| [ADR-019](ADR-019-pipeline-review-handoff.md) | Pipeline Run Review and Data Handoff Protocol | Accepted | 2026-03-06 |
| [ADR-020](ADR-020-bioaf-adapter-layer.md) | BioAF Adapter Layer (BAL) | Accepted | 2026-03-10 |
| [ADR-021](ADR-021-kubernetes-compute-backend.md) | Kubernetes as Recommended Compute Backend | Accepted | 2026-03-10 |
| [ADR-022](ADR-022-gcs-storage-backend.md) | GCS as Recommended Storage Backend | Accepted | 2026-03-10 |
| [ADR-023](ADR-023-cro-naming-profiles.md) | Configurable CRO Naming Profiles | Accepted | 2026-03-10 |
| [ADR-024](ADR-024-gcs-auto-ingest.md) | GCS Event-Driven Auto-Ingest | Accepted | 2026-03-10 |
| [ADR-025](ADR-025-automated-pipeline-triggering.md) | Automated Pipeline Triggering | Accepted | 2026-03-10 |
| [ADR-026](ADR-026-ssh-access.md) | SSH Access to Running Containers | Accepted | 2026-03-10 |
| [ADR-027](ADR-027-navigation-restructure.md) | Navigation Restructure | Accepted | 2026-03-10 |
| [ADR-028](ADR-028-bigquery-billing-export.md) | BigQuery Billing Export for Accurate Cost Data | Accepted | 2026-03-17 |
| [ADR-029](ADR-029-signed-url-direct-upload.md) | Signed URL Direct Upload for File Ingestion | Accepted | 2026-03-15 |
| [ADR-030](ADR-030-session-credentials-pam-auth.md) | Per-User Session Credentials with PAM Authentication | Accepted | 2026-03-22 |
| [ADR-031](ADR-031-notebook-image-build-pipeline.md) | Cloud Build Pipeline for Notebook Container Images | Accepted | 2026-03-22 |
| [ADR-032](ADR-032-custom-rbac.md) | Custom RBAC with Permission-Based Access Control | Accepted | 2026-03-22 |
| [ADR-033](ADR-033-versioned-compute-environments.md) | Versioned Compute Environments | Accepted | 2026-03-22 |
| [ADR-034](ADR-034-custom-work-nodes.md) | Custom Ephemeral Work Nodes | Accepted | 2026-03-22 |
| [ADR-035](ADR-035-bioaf-cli.md) | bioaf CLI for In-Session Provenance and Heartbeat | Accepted | 2026-03-22 |
| [ADR-036](ADR-036-data-export-download.md) | Data Export and Download System | Accepted | 2026-03-24 |
| [ADR-037](ADR-037-provenance-reporting.md) | Provenance Reporting System | Accepted | 2026-03-24 |
| [ADR-038](ADR-038-pipeline-io-lineage-junction.md) | Pipeline Input File Lineage as a Junction Table | Accepted | 2026-03-25 |
| [ADR-039](ADR-039-notebook-output-provenance.md) | Notebook Output Provenance | Accepted | 2026-03-25 |
| [ADR-040](ADR-040-notebook-file-lifecycle.md) | Notebook Session File Lifecycle | Accepted | 2026-04-04 |
| [ADR-041](ADR-041-environment-build-versioning.md) | Environment Build Versioning | Accepted | 2026-04-04 |

## How to Use This Directory

- **New decisions** should be added as `ADR-NNN-short-title.md` and registered in this index.
- **Superseded decisions** should have their status changed to "Superseded by ADR-NNN" rather than being deleted.
- **Proposed decisions** that haven't been accepted yet should use status "Proposed".

## Template

```markdown
# ADR-NNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-NNN
**Date:** YYYY-MM-DD
**Deciders:** Names

## Context
What is the issue that we're seeing that is motivating this decision?

## Decision
What is the change that we're proposing and/or doing?

## Rationale
Why is this the best choice among the alternatives considered?

## Consequences
What becomes easier or more difficult to do because of this change?
```
