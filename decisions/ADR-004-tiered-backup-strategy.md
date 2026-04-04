# ADR-004: Tiered Backup Strategy

**Status:** Accepted (revised 2026-04-03)
**Date:** 2026-03-05 (original), 2026-04-03 (revised)
**Deciders:** Brent (product owner)

## Context

bioAF manages several categories of data with different criticality levels, access patterns, and recovery requirements. A one-size-fits-all backup policy would either over-protect low-value data (wasteful) or under-protect high-value data (dangerous).

The experiment tracking database and audit log are the crown jewels -- if they're lost, the entire auditability story collapses. GCS data is inherently durable but vulnerable to accidental deletion. Platform configuration should be recoverable but isn't worth the same investment as scientific data.

### Revision notes (2026-04-03)

The original ADR assumed Cloud SQL for PostgreSQL and Filestore NFS for shared storage. The current architecture runs PostgreSQL in a Docker container and uses GCS exclusively for file storage. This revision aligns the backup strategy with the actual infrastructure:

- Replaced Cloud SQL PITR/snapshots (Tier 1) with pg_dump to GCS
- Removed Filestore NFS snapshots (Tier 4) entirely -- not in use
- Consolidated from 5 tiers to 4
- All backups go to GCS -- never to local filesystem

## Decision

Implement a four-tier backup strategy with different mechanisms and policies per data category. All backup data is stored in a persistent GCS bucket that survives infrastructure teardown.

### Persistent backups bucket

A dedicated `bioaf-backups-{project_id}` bucket is created in the Terraform foundation module (alongside the tfstate bucket). It uses fixed naming with no `stack_uid` suffix, so it is not destroyed during storage module teardown or redeployment. All backup tiers write to prefixed paths within this single bucket:

```text
gs://bioaf-backups-{project_id}/
  postgres/pgdump-20260403-030000.dump
  config/config-20260403-020000.json
```

### Tier 1: Database (pg_dump to GCS) -- Mandatory

- `pg_dump -Fc` (custom/compressed format) run on a configurable schedule
- Dump written to `/tmp/`, uploaded to GCS `postgres/` prefix, temp file deleted
- Default schedule: daily (configurable interval in hours)
- Default retention: 14 days
- Minimum enforceable: 1 day
- Old dumps rotated automatically after each backup
- Manual trigger available via API and UI ("Run Backup Now")
- **Cannot be disabled.** The audit log's integrity is a core product promise.

In production (GKE), a Kubernetes CronJob triggers the backend API endpoint daily. In local/dev mode, an asyncio background loop handles scheduling.

### Tier 2: Platform Configuration (GCS JSON snapshots) -- Configurable

A Kubernetes CronJob (or background loop) exports platform configuration as JSON to the `config/` prefix in the backups bucket.

- Default schedule: nightly at 2 AM
- Default retention: 30 days
- Minimum enforceable: 1 day
- Admin can adjust cadence and retention

### Tier 3: GCS Data Buckets -- Always Protected

- Object versioning enabled on all managed buckets -- **cannot be disabled via bioAF**
- Non-current version retention: 30 days default (configurable via Terraform)
- `force_destroy = false` on all buckets prevents accidental deletion
- Backup status page verifies versioning is enabled by querying the GCS API

### Tier 4: Terraform State -- Always Protected

- Stored in a persistent GCS bucket (`bioaf-tfstate-{project_id}`) with object versioning
- Every `terraform apply` preserves the previous state as a non-current version
- Not configurable -- infrastructure state must always be recoverable

## Design constraints

**GCS-only storage.** All backups must go to GCS, never the local filesystem. Local storage risks filling the node disk and causing a failure. Containers are ephemeral -- anything written inside one disappears on restart. The `/tmp/` directory is used only as a transient staging area before upload.

**Off-node by default.** The backup system must not require manual intervention, CLI commands, or local scripts. Everything is automated through the application and triggered via the UI or scheduled CronJobs.

**Persistent bucket naming.** The backups bucket uses fixed naming (`bioaf-backups-{project_id}`) with no random suffix or `stack_uid`. This means it survives `terraform destroy` of the storage module and redeployment with a new stack. Removing this bucket requires manual intervention -- by design.

## Rationale

- **pg_dump over Cloud SQL backups** because the current architecture runs PostgreSQL in Docker, not Cloud SQL. pg_dump is portable and works with any Postgres deployment. If Cloud SQL is adopted later, its native PITR/snapshots can supplement or replace pg_dump.
- **Single backups bucket** simplifies access control, lifecycle management, and monitoring. Prefixed paths (`postgres/`, `config/`) provide logical separation without the overhead of multiple buckets.
- **Foundation module placement** ensures the bucket is created before any workload runs and is never torn down by storage or compute module operations.
- **Mandatory database backups** prevent the scenario where someone disables backups to save a few dollars and then loses the audit trail or experiment history.
- **User-configurable retention** respects that different teams have different risk tolerances and budgets.

## Consequences

- The bioAF UI includes a Backup & Recovery page with per-tier status, a manual backup trigger for PostgreSQL, and snapshot history for both database and config backups.
- Database backup minimum enforcement is implemented at the application level (API rejects retention below 1 day).
- The backend Docker image must include `postgresql-client` for `pg_dump`.
- GCS bucket versioning is set during Terraform provisioning and is not exposed as toggleable in the UI.
- Backup health monitoring runs hourly: checks the age of the most recent backup in each tier and emits notification events when backups are overdue.
- The `backups_bucket_name` is stored in `platform_config` after foundation bootstrap and read by the backup service at runtime.
