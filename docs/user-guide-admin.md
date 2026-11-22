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

### One Infrastructure Action at a Time

Deploy, teardown, Destroy Storage, Check for Infrastructure Updates, cluster settings changes, image
builds, configuration sync and initialization each run as one **infrastructure operation**. Every action
asks for confirmation first; once you confirm and bioAF accepts it, it plans and applies on its own, with
no second approval. While an operation runs, those controls are unavailable to every administrator and a
banner names it; click **View status** to open the same progress as the administrator who started it.
Other roles see only that infrastructure maintenance is in progress. If two administrators confirm at
nearly the same moment, only one action starts and the other is told which one started first. Closing the
status window, refreshing or signing out does not stop an operation.

**Check for Infrastructure Updates** compares deployed infrastructure with this version of bioAF and adds
what is new. Changes that would delete or replace an existing resource are listed in the outcome and never
applied there; they take effect the next time you tear down and redeploy. An update stops at the first
real failure and reports what was applied before it.

**Cancel.** An administrator with `infrastructure:configure` can cancel any running operation from its
status window. bioAF stops at the next safe point (Terraform is asked to stop and save its state; a cloud
image build is cancelled with the provider). Work already done is not undone. If stopping cannot be
confirmed within two minutes, the operation needs recovery and other actions stay unavailable.

**After a restart.** When the bioAF server restarts during an operation and bioAF can prove the old
backend container stopped (the same container restarted), the operation is recorded as interrupted and
administrators see a one-time banner. Running the action again continues with what already exists.

**Recovery required.** When bioAF cannot prove the Terraform process stopped (for example the backend now
runs in a different container), infrastructure actions stay paused. To recover:

1. Open the banner's **View details**.
2. On the bioAF host, confirm no Terraform process is running, for example
   `docker compose exec backend ps -ef | grep '[t]erraform'` and `ps -ef | grep '[t]erraform'` on the host.
3. Enter a reason, tick the confirmation, and click **Mark as recovered**. This needs
   `infrastructure:configure`, is recorded in the audit log, and clears the stale Terraform lock. It is
   refused while bioAF itself still sees the process running.

Retrying an action through the API with the same `Idempotency-Key` header returns the same operation.

### Storage Health and Bucket Confirmation

The **Storage** section of Components shows each bucket's state (Available, Not found, No access, Not
responding, Not confirmed, Does not match bioAF's records, Not mounted, Cannot check). Checking is
read-only: bioAF never creates or recreates a bucket from a check. A missing bucket is created again only by
**Check for Infrastructure Updates**.

bioAF records a bucket as its own when the provider's evidence matches a recorded storage deployment: the
owner (the Google Cloud project, or on AWS the installation's account, checked with the bucket permissions
the installer grants) and either bioAF's `bioaf_marker` label or tag from the deployment or, on Google
Cloud, the bucket's creation time. On an installation deployed before this release that evidence may be
missing; uploads, downloads, pipelines and the Plot Archive keep working, but Destroy Storage and automatic
repairs are unavailable until an administrator uses **Review and confirm buckets**, which records an
audited baseline as of that day. On AWS, run **Check for Infrastructure Updates** first so the buckets carry
bioAF's marker (ownership alone cannot tell a bucket from one recreated later). A bucket that does not
match bioAF's records (a different owner, a different marker, or a different bucket under the same name)
is never used, emptied or deleted, and cannot be confirmed. While a confirmed bucket has been replaced by another with the same name,
or cannot be checked, storage deploys, **Check for Infrastructure Updates** and storage destroys are
refused, so an update never gives the replacement the confirmed bucket's marker.

### Uploads and Destroy Storage

Every upload is recorded before any byte is sent, and refused before anything is sent while its storage is
missing, refused or does not match bioAF's records. Direct uploads from the browser go to a provider upload session
(a Google Cloud Storage resumable session or an S3 multipart upload) that bioAF can check and cancel.

If the bytes arrive but filing them into their experiment fails, the uploader sees "Received, not yet
filed" with a reference, and bioAF retries at 1, 5 and 15 minutes. After that the uploader (or an admin or
comp_bio user under **Data & Files > Files**) can **Retry now** or **Cancel upload**. Retrying never creates
a second file record or notification. An upload that finished in storage but was never reported back (for
example the browser closed) is filed automatically.

Cancelling an upload that is still being sent stops it from ever being filed, but it counts as a running
upload until storage confirms it stopped. The attention list shows why it stopped (Cancelled, Upload
interrupted, or Timed out after 1 hour without progress) and that bioAF is checking that the upload has
stopped. bioAF settles these in the background, and cancels a session that has received no new bytes for
an hour. Upload links issued before this release count until an hour after they expired; after that the
stored object decides. When bioAF cannot confirm whether an upload has stopped, it keeps checking, and an
administrator with infrastructure configure can choose **Stop waiting…** with a reason, acknowledging that
Destroy Storage could delete the file while it is still uploading. Nothing is deleted by that step, and it
is recorded in the audit log. Lab document and reference dataset uploads are tracked the same way; each
reference upload is stored under its own path, so a new upload never shares objects with an aborted one.

If storage has not yet confirmed the last bytes when an upload finishes, the uploader shows **Confirming
upload** and checks again automatically.

bioAF's own background writes (thumbnails, QC metric caches, data export archives, environment build
contexts and reference imports), and filing received uploads into their experiments, are admitted the same
way: while uploads are paused they are skipped, postponed or refused before anything is written ("An
administrator has paused uploads and file saving.").

**Destroy Storage** first checks every bucket, then pauses new uploads and gives running ones 30 seconds to
finish. If any are still running, or bioAF cannot confirm they stopped, nothing is deleted, the action ends
(other infrastructure actions stay available) and uploads stay paused until you choose **Allow uploads** or
run Destroy Storage again. When every remaining upload has a known settling time, the result says when to
try again; uploads bioAF cannot confirm have stopped are listed with **Stop waiting…**, and bioAF's own
work is listed by activity (for example "Creating a data export"). A bucket the provider confirms no
longer exists is treated as already removed. A bucket whose identity cannot be read now is not emptied. A
refused bucket stops the destroy with what was done recorded; fix the cause and run it again to continue.
Files are marked as deleted as each object is confirmed deleted.

### Plot Archive Health

When the results storage is missing, refused or known not to match bioAF's records, the Plot Archive
says new plots are not being added (a readable bucket that is only waiting for confirmation keeps supplying
plots) and bioAF checks the storage every 30 minutes instead of every minute; a temporary failure is
retried with increasing waits up to 30 minutes. Administrators can **Check again**. Existing plots are not
affected.

### Infrastructure Changes After an Update

After a bioAF update whose infrastructure definitions differ from what this installation last applied,
each administrator with `infrastructure:deploy` sees a popup once. **Apply now** starts Check for
Infrastructure Updates; **Later** only records that you saw it, and the Infrastructure updates card keeps a
reminder until the changes are applied. The update itself never changes cloud resources. An installation
from before this release reports that bioAF cannot tell what was applied and offers **Check now**.

### Notebook Session Cleanup

A notebook session that fails, stops, or never finishes starting has its pod and LoadBalancer Service deleted
by a background pass that runs every minute. This also works after a backend restart. Before deleting
anything, bioAF checks that the resource carries the session's own labels (session, user and type) and that
the cluster is the one the session was launched on. A deletion counts as finished only once the resource is
confirmed gone; until then it is retried. A session that was running when it failed has its outputs saved first.
If saving a session's home directory or outputs fails (for example, during a storage outage), its pod and
Service are kept and the save is retried before anything is deleted. A new session is not started at all
unless bioAF can confirm which cluster it would run on (project or account, location, and cluster name).

The progress is recorded on the session as `provider_metadata.lifecycle.cleanup.state`:

- `pending`: deletion is in progress or will be retried (for example after a permission or network error).
- `complete`: both resources are confirmed gone.
- `blocked`: bioAF will not delete automatically, because a resource under the recorded name belongs to
  another session or the cluster has changed since launch. The backend log names the session and the reason.

To escalate a `blocked` session or a log warning about notebook resources "with no session record", inspect
the named pod or Service in the `bioaf-notebooks` namespace and delete it by hand only after confirming it is
not in use. bioAF never deletes a resource whose owner it cannot verify.

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

## System Settings and Installation Metrics

**Settings > System Settings** holds installation-wide settings. Its installation metrics section shows the
saved choice, whether reporting is on, any operator override, and the installation ID. Admins with
`infrastructure:configure` choose once, in the setup wizard or in a prompt on first entry for an installation
that has not chosen; later changes and withdrawal happen here. Nothing is sent unless **Share installation
metrics** is chosen. See [Installation Metrics](installation-metrics.md) for exactly what is sent, to whom,
and how to request deletion.

## Audit and Operational Logs

**Settings > Audit Log** shows explicitly audited actions and attribution, with filters for resource type,
action and date. The page has no actor filter. Mutations through API keys include key attribution where
audited. It is not a transcript of every file read or shell command.

Application JSON logs provide operational diagnostics; the Activity Feed presents team events. Their
purposes and retention differ from the immutable Audit Log. See [Logging](deployment-guide.md#logging)
and [SSH and Exec Access](guides/ssh-access.md#persistence-and-auditing).
