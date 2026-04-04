# Architecture Decision Records

| ADR | Title | Summary |
|-----|-------|---------|
| [ADR-001](../decisions/ADR-001-gcp-only.md) | GCP-Only Infrastructure | Target GCP exclusively to reduce complexity and leverage managed services |
| [ADR-002](../decisions/ADR-002-mandatory-optional-split.md) | Mandatory/Optional Component Split | Separate core platform from optional components for flexible deployment |
| [ADR-003](../decisions/ADR-003-email-based-auth.md) | Email-Based Authentication | Use email/password auth with JWT tokens instead of OAuth/SSO for simplicity |
| [ADR-004](../decisions/ADR-004-tiered-backup-strategy.md) | Tiered Backup Strategy | 4-tier GCS-only backups: pg_dump, GCS versioning, platform config snapshots, terraform state |
| [ADR-005](../decisions/ADR-005-github-based-upgrades.md) | GitHub-Based Upgrades | Use GitHub Releases for version checking and upgrade distribution |
| [ADR-006](../decisions/ADR-006-experiment-tracking-as-foundation.md) | Experiment Tracking as Foundation | Build experiment lifecycle tracking as the core data model |
| [ADR-007](../decisions/ADR-007-ui-driven-terraform.md) | UI-Driven Terraform | Users never touch HCL; all infrastructure changes through the web UI |
| [ADR-008](../decisions/ADR-008-secret-manager.md) | Secret Manager Integration | Store all secrets in GCP Secret Manager, never in config files |
| [ADR-009](../decisions/ADR-009-immutable-audit-log.md) | Immutable Audit Log | Append-only audit trail for compliance; no UPDATE or DELETE operations |
| [ADR-010](../decisions/ADR-010-notification-system.md) | Notification System | In-process event bus with channel adapters (in-app, email, Slack) |
| [ADR-011](../decisions/ADR-011-scrna-seq-initial-scope.md) | scRNA-seq Initial Scope | Focus on single-cell RNA-seq as the primary workflow |
| [ADR-012](../decisions/ADR-012-data-portability.md) | Data Portability | All data accessible with standard tools after platform teardown |
| [ADR-013](../decisions/ADR-013-minseqe-compliant-metadata.md) | MINSEQE-Compliant Metadata | Follow MINSEQE standards for experiment and sample metadata |
| [ADR-014](../decisions/ADR-014-geo-export-service.md) | GEO Export Service | Support exporting data in GEO-compliant format |
| [ADR-015](../decisions/ADR-015-analysis-snapshot-sdk.md) | Analysis Snapshot SDK | Capture reproducible analysis snapshots with environment and parameters |
| [ADR-016](../decisions/ADR-016-snapshot-comparison-ui.md) | Snapshot Comparison UI | Visual diff tool for comparing analysis snapshots |
| [ADR-017](../decisions/ADR-017-reference-data-management.md) | Reference Data Management | Centralized management of reference genomes and annotations |
| [ADR-018](../decisions/ADR-018-cross-experiment-projects.md) | Cross-Experiment Projects | Group related experiments into projects for organization |
| [ADR-019](../decisions/ADR-019-pipeline-review-handoff.md) | Pipeline Review Handoff | Review gate between pipeline completion and data handoff |
| [ADR-020](../decisions/ADR-020-bioaf-adapter-layer.md) | BioAF Adapter Layer (BAL) | Abstract compute, storage, and notebook providers behind clean interfaces |
| [ADR-021](../decisions/ADR-021-kubernetes-compute-backend.md) | Kubernetes Compute Backend | GKE Autopilot as the recommended compute backend |
| [ADR-022](../decisions/ADR-022-gcs-storage-backend.md) | GCS Storage Backend | GCS as the recommended storage backend, replacing Filestore |
| [ADR-023](../decisions/ADR-023-cro-naming-profiles.md) | CRO Naming Profiles | Configurable naming profiles for CRO file conventions |
| [ADR-024](../decisions/ADR-024-gcs-auto-ingest.md) | GCS Auto-Ingest | Event-driven file cataloging from GCS ingest bucket via Pub/Sub |
| [ADR-025](../decisions/ADR-025-automated-pipeline-triggering.md) | Automated Pipeline Triggering | Auto-trigger pipelines when ingest conditions are met |
| [ADR-026](../decisions/ADR-026-ssh-access.md) | SSH Access | One-click kubectl exec into running containers |
| [ADR-027](../decisions/ADR-027-navigation-restructure.md) | Navigation Restructure | Reorganize sidebar navigation for clarity |
| [ADR-028](../decisions/ADR-028-bigquery-billing-export.md) | BigQuery Billing Export | Use GCP BigQuery billing export for accurate cost data |
| [ADR-029](../decisions/ADR-029-signed-url-direct-upload.md) | Signed URL Direct Upload | Browser uploads directly to GCS via signed URLs, bypassing backend |
| [ADR-030](../decisions/ADR-030-session-credentials-pam-auth.md) | Session Credentials with PAM Auth | Per-user session credentials for RStudio PAM authentication |
| [ADR-031](../decisions/ADR-031-notebook-image-build-pipeline.md) | Notebook Image Build Pipeline | Cloud Build pipeline for notebook container images |
| [ADR-032](../decisions/ADR-032-custom-rbac.md) | Custom RBAC | Permission-based access control with custom roles |
| [ADR-033](../decisions/ADR-033-versioned-compute-environments.md) | Versioned Compute Environments | Immutable, versioned notebook and compute environments |
| [ADR-034](../decisions/ADR-034-custom-work-nodes.md) | Custom Work Nodes | Ephemeral Kubernetes pods for interactive compute sessions |
| [ADR-035](../decisions/ADR-035-bioaf-cli.md) | bioaf CLI | In-session CLI for provenance capture and heartbeat |
| [ADR-036](../decisions/ADR-036-data-export-download.md) | Data Export and Download | Bulk export and download system for experiment data |
| [ADR-037](../decisions/ADR-037-provenance-reporting.md) | Provenance Reporting | Full lineage reports for files and analysis outputs |
| [ADR-038](../decisions/ADR-038-pipeline-io-lineage-junction.md) | Pipeline I/O Lineage | Junction table tracking pipeline input file lineage |
| [ADR-039](../decisions/ADR-039-notebook-output-provenance.md) | Notebook Output Provenance | Provenance tracking for notebook-generated outputs |
