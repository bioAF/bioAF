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

## Notifications

### Slack Integration (Settings)

1. Go to **Settings > Slack Integration**
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

### SMTP (Settings)

Configure SMTP for email notifications:

- Host, port, credentials, from address
- Required for email invitations and email notifications

## Platform Upgrades

In **Settings** under Platform Version:

- View current version and check for updates
- Review changelog before upgrading
- Upgrade history with status tracking
- bioAF checks for updates daily and shows a notification when available

## Audit Log

Navigate to **Settings > Audit Log**:

- View who accessed what resource and when
- Filter by user, resource type, action, and date range
- Useful for compliance and auditing

## Settings Overview

The **Settings** page centralizes:

- SMTP configuration
- Slack integration (OAuth setup, channel mappings)
- Platform version and upgrades
- Test notification delivery

## Tips

- The **Dashboard** shows admin-specific metrics: cost summary, system health, experiment counts
- Use **Activity Feed** to monitor team activity
- Set up Slack integration early for real-time alerts
- Review access logs periodically for security compliance
