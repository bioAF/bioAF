# Admin User Guide

This guide covers bioAF administration: user management, infrastructure components, cost monitoring, backups, notifications, and platform upgrades.

## User Management

Navigate to **Settings > Users** (and **Settings > Roles & Permissions**):

- View all users with their roles and status
- Change user roles: admin, comp_bio, bench, viewer
- Deactivate users who have left
- Invite new users via email

### Roles

| Role | Access |
|------|--------|
| admin | Full platform access, settings, user management |
| comp_bio | Pipelines, notebooks, environments |
| bench | Experiments, samples, data upload, results |
| viewer | Read-only access to experiments and results |

## Infrastructure Components

Navigate to **Infrastructure > Components**:

- Enable/disable infrastructure components through the UI
- Each toggle triggers a Terraform apply
- View component health status
- Dependencies are enforced (e.g., JupyterHub requires SLURM)

### Deployment Recovery

If a deployment times out (e.g., due to a Google Cloud service delay), the app
automatically detects orphaned clusters on your next visit:

- **Cluster came online**: you are prompted to resume where you left off or start fresh
- **Cluster still provisioning**: you are told Google Cloud is still working and to check back later
- **Cluster failed or disappeared**: cleaned up automatically in the background

The deploy button is disabled while orphaned resources exist to prevent duplicate clusters.

## Cost Center

Navigate to **Infrastructure > Cost Center**:

- **Current month spend** with daily trend
- **Breakdown by component** showing which services cost the most
- **Budget configuration**: set monthly budget and alert thresholds
- **Projected month-end** based on current daily average

### Budget Alerts

Configure alerts at 50%, 80%, and 100% of budget:

- Notifications sent via configured channels (in-app, email, Slack)
- Optional **scale-to-zero** at 100% stops compute workloads automatically

## Backup & Recovery

Navigate to **Infrastructure > Backup & Recovery**:

- View backup status for each tier: PostgreSQL (pg_dump), GCS Object Versioning, Platform Config, Terraform State
- Each tier shows: last backup, size, next scheduled, retention policy, backup count
- **On-demand backups**: trigger database or config backups from the UI
- **PostgreSQL snapshots**: browse backup history, restore with 1-hour review period
- **Config snapshots**: browse and diff configuration backups
- **Terraform state**: list and download state files
- **Settings**: adjust backup schedule (hours) and retention (days) for database and config

### When a database backup or restore fails

- The error shows a reference such as `backup-1a2b3c4d` or `restore-1a2b3c4d`. Search the backend log for it
  (`docker compose logs backend | grep <reference>`): the log lines give the phase (download, create_database,
  pg_restore, dump, upload), the tool's exit status and a category (`connection`, `authentication`, `disk_full`,
  `version_mismatch`, `archive_unreadable`, `permission`, `sql_error`, `unclassified`). They never contain the
  database tool's own output or SQL, which can include research data.
- One database backup and one restore run at a time. Another request while one runs is refused with a "try again"
  message; nothing is queued. A retry after a failure starts from the beginning.
- A backup never replaces an existing backup with the same name. If two land in the same second, the second is
  refused; try again.
- The dump file lives in a private directory under `/tmp/bioaf-backups` inside the backend container
  (`BIOAF_BACKUP_SCRATCH_DIR`) only while the operation runs, and is removed when it ends, succeeded or not.
- If a restore reports that its database import could not be stopped, no new restore can start. Restart bioAF
  (`./bioaf restart`), confirm the application is using the `bioaf` database, then drop the leftover review copy
  with `DROP DATABASE IF EXISTS bioaf_restore;` in `psql`. The leftover dump directory is removed by the next restore.

## Notifications

### Slack Integration

1. Go to **Settings > Integrations > Slack**
2. Click **Generate Slack App Manifest** and copy the JSON
3. Go to [api.slack.com/apps](https://api.slack.com/apps), click **Create New App > From a manifest**, paste the JSON
4. Copy the Client ID, Client Secret, and Signing Secret from the app's Basic Information page
5. Paste them into the credentials form in bioAF and click **Save Credentials**
6. Click **Add to Slack** and approve the app for your workspace
7. Add channel mappings to choose which channels receive which event types
8. Click **Test Channel Mappings** to verify delivery

### Notification Rules

Configure which events trigger notifications and through which channels:

- Pipeline completions/failures
- Budget threshold alerts
- Backup failures
- Component health issues

### SMTP

Configure SMTP in **Settings > Integrations > SMTP**:

- Host, port, credentials, from address
- Required for email invitations and email notifications

## Platform Upgrades

In **Settings > Information**:

- View current version and check for updates
- Review changelog before upgrading
- Click **Install Update** to update directly from the UI
- Upgrade history with status tracking
- bioAF checks for updates daily and shows a notification when available
- Updates can also be run from the CLI: `./bioaf update [version]`

## Audit Log

Navigate to **Settings > Audit Log**:

- View who accessed what resource and when
- Filter by user, resource type, action, and date range
- Useful for compliance and auditing

## Settings Overview

The **Settings** section includes:

- **Users** -- user management and invitations
- **Roles & Permissions** -- built-in and custom roles
- **Audit Log** -- immutable activity trail
- **Integrations** -- SMTP, Slack, GCP, and Seqera configuration
- **Information** -- platform version, updates, and upgrade history

## Tips

- The **Dashboard** shows admin-specific metrics: cost summary, system health, experiment counts
- Use **Activity Feed** to monitor team activity
- Set up Slack integration early for real-time alerts
- Review Access Logs periodically for security compliance
