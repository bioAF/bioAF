# Architecture Decision Records

| ADR | Title | Summary |
|-----|-------|---------|
| [ADR-001](../decisions/ADR-001-gcp-only.md) | GCP-Only Infrastructure | Target GCP exclusively to reduce complexity and leverage managed services |
| [ADR-002](../decisions/ADR-002-mandatory-optional-split.md) | Mandatory/Optional Component Split | Separate core platform from optional components for flexible deployment |
| [ADR-003](../decisions/ADR-003-email-based-auth.md) | Email-Based Authentication | Use email/password auth with JWT tokens instead of OAuth/SSO for simplicity |
| [ADR-004](../decisions/ADR-004-tiered-backup-strategy.md) | Tiered Backup Strategy | 4-tier GCS-only backups: pg_dump, GCS versioning, platform config snapshots, terraform state |
| [ADR-005](../decisions/ADR-005-github-based-upgrades.md) | GitHub-Based Upgrades | Use GitHub Releases for version checking and upgrade distribution |
| [ADR-006](../decisions/ADR-006-experiment-tracking-as-foundation.md) | Experiment Tracking as Foundation | Build experiment lifecycle tracking as the core data model |
| [ADR-007](../decisions/ADR-007-ui-driven-terraform.md) | UI-Driven Terraform | Users never touch HCL; all infrastructure changes through the web UI (execution superseded by [ADR-066](../decisions/ADR-066-terraform-single-execution-owner.md): per-module TerraformExecutor is the single owner) |
| [ADR-008](../decisions/ADR-008-secret-manager.md) | Secret Manager Integration | Store all secrets in GCP Secret Manager, never in config files |
| [ADR-009](../decisions/ADR-009-immutable-audit-log.md) | Immutable Audit Log | Append-only audit trail for compliance; no UPDATE or DELETE operations |
| [ADR-010](../decisions/ADR-010-notification-system.md) | Notification System | In-process event bus with channel adapters (in-app, email, Slack) |
| [ADR-011](../decisions/ADR-011-scrna-seq-initial-scope.md) | scRNA-seq Initial Scope | Focus on single-cell RNA-seq as the primary workflow |
| [ADR-012](../decisions/ADR-012-data-portability.md) | Data Portability | All data accessible with standard tools after platform teardown |
| [ADR-013](../decisions/ADR-013-minseqe-compliant-metadata.md) | MINSEQE-Compliant Metadata | Follow MINSEQE standards for experiment and sample metadata |
| [ADR-014](../decisions/ADR-014-geo-export-service.md) | GEO Export Service | Support exporting data in GEO-compliant format |
| [ADR-015](../decisions/ADR-015-analysis-snapshot-sdk.md) | Analysis Snapshot SDK | Capture reproducible analysis snapshots with environment and parameters |
| [ADR-016](../decisions/ADR-016-snapshot-comparison-ui.md) | Snapshot Comparison UI | Visual diff tool for comparing analysis snapshots |
| [ADR-017](../decisions/ADR-017-reference-data-management.md) | Reference Data Management | Centralized management of Reference Datasets |
| [ADR-018](../decisions/ADR-018-cross-experiment-projects.md) | Cross-Experiment Projects | Group related experiments into projects for organization |
| [ADR-019](../decisions/ADR-019-pipeline-review-handoff.md) | Pipeline Review Handoff | Review Handoff gate between pipeline completion and data handoff |
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
| [ADR-034](../decisions/ADR-034-custom-work-nodes.md) | Custom Work Nodes | Custom ephemeral Work Nodes (originally Kubernetes pods; moved to GCE VMs by [ADR-043](../decisions/ADR-043-work-nodes-gce-migration.md)) |
| [ADR-035](../decisions/ADR-035-bioaf-cli.md) | bioaf CLI | In-session CLI for provenance capture and heartbeat |
| [ADR-036](../decisions/ADR-036-data-export-download.md) | Data Export and Download | Bulk export and download system for experiment data |
| [ADR-037](../decisions/ADR-037-provenance-reporting.md) | Provenance Reporting | Full lineage reports for files and analysis outputs |
| [ADR-038](../decisions/ADR-038-pipeline-io-lineage-junction.md) | Pipeline I/O Lineage | Junction table tracking pipeline input file lineage |
| [ADR-039](../decisions/ADR-039-notebook-output-provenance.md) | Notebook Output Provenance | Provenance tracking for notebook-generated outputs |
| [ADR-040](../decisions/ADR-040-notebook-file-lifecycle.md) | Notebook Session File Lifecycle | Lifecycle and persistence rules for files created in notebook sessions |
| [ADR-041](../decisions/ADR-041-environment-build-versioning.md) | Environment Build Versioning | Versioned, immutable builds for compute environments |
| [ADR-042](../decisions/ADR-042-spot-preemption-retry-strategy.md) | Spot Preemption Retry Strategy | Retry pipeline tasks on spot-VM preemption without re-requesting extra capacity |
| [ADR-043](../decisions/ADR-043-work-nodes-gce-migration.md) | Work Nodes GCE Migration | Work Nodes run on dedicated GCE VMs instead of GKE pods (supersedes the ADR-034 pod model) |
| [ADR-044](../decisions/ADR-044-custom-pipelines.md) | Custom Pipelines | User-defined custom pipelines alongside the nf-core registry |
| [ADR-045](../decisions/ADR-045-pipeline-environments.md) | Pipeline Environments | Named, versioned environments for pipeline execution |
| [ADR-046](../decisions/ADR-046-pipeline-version-cascade.md) | Pipeline Version Cascade | Propagate pipeline version changes downstream via the event bus |
| [ADR-047](../decisions/ADR-047-data-at-rest-encryption.md) | Data-at-Rest Encryption | App-level Fernet encryption for sensitive stored fields |
| [ADR-048](../decisions/ADR-048-public-integration-api-surface.md) | Public Integration API Surface | Stable public `/v1/integrations` LIMS API surface |
| [ADR-049](../decisions/ADR-049-service-accounts-and-api-keys.md) | Service Accounts and API Keys | Service-account + API-key authentication for programmatic access |
| [ADR-050](../decisions/ADR-050-external-ids-and-idempotent-writes.md) | External IDs and Idempotent Writes | External-id-keyed idempotent writes on the integration API |
| [ADR-051](../decisions/ADR-051-outbound-webhook-delivery.md) | Outbound Webhook Delivery | Deliver event notifications to external systems via outbound webhooks |
| [ADR-052](../decisions/ADR-052-llm-integration-trust-boundary.md) | LLM Integration Trust Boundary | LLM output is advisory; defined trust boundary around model inputs/outputs |
| [ADR-053](../decisions/ADR-053-llm-provider-abstraction.md) | LLM Provider Abstraction | Single-active LLM provider behind a provider abstraction |
| [ADR-054](../decisions/ADR-054-gemma-per-request-inference.md) | Gemma Per-Request Inference | Self-hosted Gemma served as a per-request GCE inference pipeline |
| [ADR-055](../decisions/ADR-055-agent-review-advisory-entity.md) | Agent Review Advisory Entity | Agent Review is an advisory entity, not a gating control |
| [ADR-056](../decisions/ADR-056-literature-library-domain-model.md) | Literature Library Domain Model | Domain model for the literature library (papers, authors, associations) |
| [ADR-057](../decisions/ADR-057-literature-as-input-to-agent-review.md) | Literature as Agent Review Input | Feed library literature into Agent Review prompts |
| [ADR-058](../decisions/ADR-058-naming-profile-parse-only.md) | Naming Profiles Parse-Only | Naming profiles parse filenames only, with template-driven vocabulary |
| [ADR-059](../decisions/ADR-059-lab-knowledge-institutional-memory.md) | Lab Knowledge Institutional Memory | Lab knowledge as a persistent institutional-memory layer |
| [ADR-060](../decisions/ADR-060-lab-document-tag-organization.md) | Lab Document Tag Organization | Tag-based organization for lab documents |
| [ADR-061](../decisions/ADR-061-lab-document-versioning.md) | Lab Document Versioning | Upload-new-version model for lab document versioning |
| [ADR-062](../decisions/ADR-062-glossary-ai-population-human-review.md) | Glossary AI Population + Human Review | AI proposes glossary terms; mandatory human review before adoption |
| [ADR-063](../decisions/ADR-063-sdr-status-machine.md) | SDR Status Machine | Status machine for Scientific Decision Records (SDR) |
| [ADR-064](../decisions/ADR-064-sdr-reassessment-triggers.md) | SDR Re-Assessment Triggers | Date-based triggers for re-assessing Scientific Decision Records |
| [ADR-065](../decisions/ADR-065-bal-normalized-contract.md) | BAL Normalized Contract | Normalized BAL model, capabilities, and the category/backend rule (supersedes ADR-020) |
| [ADR-066](../decisions/ADR-066-terraform-single-execution-owner.md) | Terraform Single Execution Owner | Per-module TerraformExecutor is the sole terraform engine (supersedes the ADR-007 implementation) |
