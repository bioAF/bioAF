### Infrastructure operations

- Deploy, teardown, Destroy Storage, Check for Infrastructure Updates, cluster settings changes, image
  builds, configuration sync and initialization now run one at a time across the installation. While one
  runs, every administrator's conflicting controls are unavailable and a banner names it; administrators
  open the same live status from the banner on any page. A second request is refused and told which action
  started first, instead of overlapping it.
- Each status window shows its own action's steps and outcome. A failure before Terraform starts (for
  example while emptying a bucket) is reported as that action's failure, never as an earlier run's success.
- **Check for Infrastructure Updates** now asks for confirmation first, then plans and applies additive
  changes automatically with no second approval. Changes that would delete or replace an existing resource
  are listed and never applied. An update stops at the first real failure.
- Any administrator with infrastructure configure can **Cancel** a running action from its status window.
  bioAF stops at the next safe point and never undoes finished work. This replaces **Abort deployment**.
  Cancelling a notebook image build is now reported only once the build provider confirms it. A
  cancellation made before the action starts prevents it from running.
- Component changes are refused while another infrastructure action runs.
- After a server restart, an interrupted action is reported once to administrators. When bioAF cannot prove
  Terraform stopped, infrastructure actions pause until an administrator records the recovery (audited). An
  image build still running in the cloud keeps other actions unavailable until its provider reports it
  finished.
- An infrastructure update whose plan for a change fails is reported as failed, not up to date.
- Retried API requests with the same `Idempotency-Key` return the same operation.

### Storage

- Destroy Storage no longer stops on a bucket that was already deleted outside bioAF, retries an
  unresponsive provider, reports a refused bucket with what to fix, and continues where it stopped when run
  again. It pauses uploads first and deletes nothing while uploads are still running; it then ends, says
  when to try again when it can, and leaves other infrastructure actions available. Files are marked as
  deleted as each object is confirmed deleted. A bucket whose owner or creation time cannot be read is
  never emptied.
- A new storage deployment after Destroy Storage creates buckets with new names; a retry after a failed
  storage deployment reuses the same names.
- Components shows each bucket's health (Available, Not found, No access, and so on) with **Check again**.
  Checking never creates a bucket.
- bioAF no longer takes bucket names from Terraform state alone.

### Uploads

- An upload whose bytes arrived but could not be filed into its experiment is kept, retried automatically,
  and can be retried or cancelled by the uploader (and by admins and comp_bio users from **Files**). Upload
  errors no longer show internal storage addresses; they show a plain message and a reference.
- While storage is being destroyed, new uploads are refused before anything is sent. Uploads are also
  refused before anything is sent when their storage is missing, refused or belongs to another
  installation.
- Browser uploads now go to a storage upload session that bioAF can check and cancel (a resumable session
  on Google Cloud, a multipart upload on AWS, which also lifts the 5 GiB single-upload limit there).
  Cancelling an upload that is still being sent stops it from being filed, and it counts as running until
  storage confirms it stopped. Sessions with no new bytes for 24 hours are cancelled. An upload that
  finished in storage but was never reported back is filed automatically, and filing happens once even
  when requests overlap.
- An administrator with infrastructure configure can release an upload storage could not confirm stopped,
  with a reason and an acknowledgement that the transfer might still exist (audited).
- Lab document and reference dataset uploads, and bioAF's own background writes (thumbnails, QC caches,
  export archives, environment build contexts, reference imports), count as storage writers too.

### Plot Archive

- When the results storage is missing or unreadable, the Plot Archive says new plots are not being added,
  and bioAF checks the storage every 30 minutes instead of every minute.

### After an update

- When a bioAF update brings infrastructure changes, each administrator with infrastructure deploy is asked
  once to apply them. Updating bioAF itself never changes cloud resources.

### Upgrade notes

- New infrastructure buckets carry a `storage_uid` label (GCP) or tag (AWS). Existing buckets get it only
  when an administrator applies Check for Infrastructure Updates.
- On installations deployed before this release, the storage buckets may show **Not confirmed**. Uploads,
  downloads and pipelines keep working; an administrator confirms the buckets under Infrastructure >
  Components before Destroy Storage is available.
- Uploads that were waiting in memory for completion when the update restarted the backend cannot be
  completed; upload those files again.
- Upload links issued before this release count as running uploads until an hour after they expire; then
  bioAF files any upload that arrived, and lists the rest for an administrator to release.
