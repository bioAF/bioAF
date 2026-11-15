# Admin User Guide

Administration covers access, infrastructure, cost reporting, recovery and updates. Navigation and mutation
controls depend on effective grants, enabled components and provider capabilities.

## User Management

Open **Settings > Users & Accounts** to manage users, service accounts, API keys and webhooks. Use
**Settings > Roles & Permissions** for built-in/custom roles. Built-in `admin`, `comp_bio`, `bench` and `viewer`
roles are starting permission sets, not a substitute for checking the relevant resource/action grant.

Lock, Deactivate and Reactivate require `users:deactivate`; Delete requires `users:delete`. Those controls
are hidden while grants are unknown and are checked again on confirmation. Deletion also has record-state
and audit-history restrictions; deactivation preserves attribution for users who have performed work.

Service accounts cannot use browser login. Their API-key scopes are intersected with the account's role;
changing the role does not rewrite existing keys. See [API Authentication](api/auth.md).

## Infrastructure Components

Open **Infrastructure > Components** to provision and manage the compute stack and optional components.
Terraform operations report progress and must reach a confirmed outcome. Components can wait for compute,
storage or image-build prerequisites; a toggle is not always an immediate independent Terraform apply.
Notebook components use Kubernetes, not a required SLURM cluster.

GCP is the reference deployment and AWS parity is incomplete. See [Compute Stack Setup](guides/compute-stack-setup.md)
for supported paths. Avoid treating local simulation as a successful real deployment.

### Deployment Recovery

When cloud resources outlive an interrupted deployment, the page reports their state and offers authorized
recovery actions. A ready orphaned cluster can be resumed or removed; provisioning resources may need more
time. Deploy is blocked while conflicting resources remain. Resume and teardown require
`infrastructure:deploy` and server-side ownership validation. A read-only role cannot recover resources
merely because it can see their status.

## Cost Center

Open **Infrastructure > Cost Center** for spend, component breakdown, budgets and projections. GCP billing
export imports are bounded; data availability and billing delay affect what is shown. Cost widgets stay
hidden on the Dashboard until billing is configured. The Cost Center itself can show estimated costs
with a banner explaining that billing export is not configured.

Daily records use UTC usage dates, and the current month follows the UTC calendar month, independent of
the server/browser timezone. Imported billing usage is not grouped by invoice month. Configure alerts and
supported budget actions in the Cost Center; review an action before relying on it to limit actual cloud spend.
The experiment auto-run budget guard is documented separately in [Automated Pipelines](guides/automated-pipelines.md).

## Backup & Recovery

Open **Infrastructure > Backup & Recovery**. Actions require the relevant backup view/create/restore grants:

- View tiers for PostgreSQL (pg_dump), provider-specific Object Versioning, Platform Configuration and Terraform State
- Each tier shows: last backup, size, next scheduled, retention policy, backup count
- **On-demand backups**: trigger database or config backups from the UI
- **PostgreSQL snapshots**: browse backup history, restore with 1-hour review period
- **Config snapshots**: browse and diff configuration backups
- **Terraform state**: list and download state files
- **Settings**: adjust backup schedule (hours) and retention (days) for database and config

### Review a Restored Database

1. Select a snapshot and confirm the restore. An inventoried legacy backup without a creation-time checksum
   requires an additional explicit, audited opt-in; missing/damaged checksum records are refused.
2. Wait for download, integrity/capacity checks and the strict import to finish. Preparation errors leave
   application traffic on the current database.
3. Once ready, bioAF pauses automatic work and switches application reads to the restored review copy.
   The review banner offers **Accept** and **Reject** without a page reload.
4. Browse the restored records within the one-hour review period. **Accept** makes the restored copy
   permanent; **Reject** returns to the original database. Automatic work resumes after a successful decision.
5. An expired review cannot be accepted, even if timeout cleanup has not run yet. The timeout attempts to
   revert to the original database. A failed revert or partial database rename requires operator recovery;
   do not start another restore or drop a database until its identity and application connection are verified.

See [Configuration](configuration.md) for preparation limits and the production database-volume mount.
The UI database snapshots, `./bioaf backup` host dumps and updater recovery dumps have different formats,
storage and retention. Do not assume the UI checksum/restore workflow applies to every host dump.

### When a database backup or restore fails

- The error shows a reference such as `backup-1a2b3c4d` or `restore-1a2b3c4d`. Search the backend log for it
  (`./bioaf logs backend` from the application host checkout): the log lines give the phase (download, create_database,
  pg_restore, dump, upload), the tool's exit status and a category (`connection`, `authentication`, `disk_full`,
  `version_mismatch`, `archive_unreadable`, `permission`, `sql_error`, `unclassified`). They never contain the
  database tool's own output or SQL, which can include research data.
- One database backup and one restore run at a time. Another request while one runs is refused with a "try again"
  message; nothing is queued. A retry after a failure starts from the beginning.
- A backup never replaces an existing backup with the same name. If two land in the same second, the second is
  refused; try again.
- The dump file lives in a private directory under `/tmp/bioaf-backups` inside the backend container
  (`BIOAF_BACKUP_SCRATCH_DIR`) while the operation runs and is normally removed afterward. If a child process cannot be stopped safely, its workspace is retained and further operations are refused until recovery.
- Every backup made by bioAF records a SHA-256 checksum. The snapshot table shows it as **Checksum recorded**;
  backups made before checksums existed show **Unverified (made before checksums)** and can be restored only
  through a confirmation that says so (the restore is recorded in the audit log). **Checksum missing or damaged**
  backups cannot be restored.
- Before anything changes, a restore checks the backup's size (at most 10 GiB, `BIOAF_RESTORE_MAX_DUMP_BYTES`),
  the free disk space it leaves (at least 3 GiB or 10% of the disk), the downloaded bytes against the recorded
  checksum, and that the file is a readable archive. The import itself is all or nothing: any error leaves no
  review copy and traffic stays on the current database. Time limits: download 15 minutes, archive check
  1 minute, import 30 minutes, the whole preparation 45 minutes (`BIOAF_RESTORE_*_SECONDS`). These limits are
  provisional starting values.
- A restore also checks that the database's own disk has room for the restored copy and its transaction log
  (twice the larger of the current database and the backup) above the same floor, before creating it, and keeps
  watching while the import runs: if that disk runs low, the import is stopped and nothing is committed. The
  backend sees the database volume read-only for this (`BIOAF_RESTORE_DB_VOLUME_DIR` in `docker-compose.yml`).
- If a restore reports that its database import could not be stopped, no new restore can start. Restart bioAF
  (`./bioaf restart`), confirm the application is using the `bioaf` database, then drop the leftover review copy
  with `DROP DATABASE IF EXISTS bioaf_restore;` in `psql`. The leftover dump directory is removed by the next restore.

## Notifications

Configure SMTP and Slack under **Settings > Integrations**. Email invitations and notifications require
working SMTP settings. For Slack, use the generated app manifest, configure the app credentials, authorize
the workspace and set/test channel mappings. Manage event rules and user preferences through the relevant
notification controls.

Slack and durable outbound webhooks have different delivery/retry policies. Webhook subscribers should
deduplicate deliveries and verify signatures; see [Webhooks](api/webhooks.md).

## Platform Upgrades

Open **Settings > Platform Info** to check versions, review release notes and request an update. The host's
systemd update agent performs UI-requested updates. The host command is `./bioaf update [version]`.

Updates, setup and local builds run one at a time. The updater records current, previous and candidate
image generations, protects their recovery material, and checks free disk before staging images. The
shared build cache is reported rather than automatically pruned. Read
[Updating](deployment-guide.md#updating) and
[Disk use and deployment generations](deployment-guide.md#disk-use-and-deployment-generations) before rollback.
Image rollback does not reverse database migrations.

## Audit and Operational Logs

**Settings > Audit Log** shows explicitly audited actions and attribution, with filters for resource type,
action and date. The page has no actor filter. Mutations through API keys include key attribution where
audited. It is not a transcript of every file read or shell command.

Application JSON logs provide operational diagnostics; the Activity Feed presents team events. Their
purposes and retention differ from the immutable Audit Log. See [Logging](deployment-guide.md#logging)
and [SSH and Exec Access](guides/ssh-access.md#persistence-and-auditing).
