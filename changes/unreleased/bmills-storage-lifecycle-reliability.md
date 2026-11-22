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
  are listed and never applied. An update stops at the first real failure. A finished update says
  "Infrastructure updated." instead of a count, which called changes to existing resources "added".
- Any administrator with infrastructure configure can **Cancel** a running action from its status window.
  bioAF stops at the next safe point and never undoes finished work. This replaces **Abort deployment**.
  Cancelling a notebook image build is now reported only once the build provider confirms it. A
  cancellation made before the action starts prevents it from running.
- A component change is its own infrastructure action: no other action can start while it runs, and an
  image build it starts keeps other actions unavailable until the build finishes.
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
- A confirmed bucket that went missing and that bioAF recreated (with its identity marker, during a
  recorded storage change) is trusted again instead of being reported as another installation's.
- A bucket missing or refused outside bioAF no longer stops the other buckets' details from loading on
  Components.
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
  storage confirms it stopped. Sessions with no new bytes for an hour are cancelled, so a closed browser tab does not keep Destroy Storage waiting; browsers send Google Cloud uploads in chunks so storage records progress. An upload that
  finished in storage but was never reported back is filed automatically, and filing happens once even
  when requests overlap.
- When bioAF cannot confirm whether an upload has stopped, an administrator with infrastructure configure
  can choose **Stop waiting…**, with a reason and an acknowledgement that Destroy Storage could delete the
  file while it is still uploading (audited; nothing is deleted by that step). The attention list shows
  why an upload was stopped: cancelled, interrupted, or timed out after an hour without progress.
- While storage confirms the last bytes of an upload, the uploader shows **Confirming upload** instead of
  an error.
- Uploads with a declared checksum are verified against it even when bioAF finds them finished on its own.
- An upload waiting to be filed is shown as done only once it is filed. If it is cancelled elsewhere (for
  example from **Files**) the uploader shows it as cancelled, and if filing fails for good it shows an error
  asking for the file to be uploaded again.
- Each reference dataset upload is stored under its own path.
- Lab document and reference dataset uploads, filing received uploads, and bioAF's own background writes
  (thumbnails, QC caches, export archives, environment build contexts, reference imports) count as storage
  writers too, and are paused with uploads.

### Plot Archive

- When the results storage is missing, unreadable, or known to belong to another installation, the Plot
  Archive says new plots are not being added, and bioAF checks the storage every 30 minutes instead of
  every minute. A readable results bucket that is only waiting for confirmation keeps supplying new plots.

### After an update

- When a bioAF update brings infrastructure changes, each administrator with infrastructure deploy is asked
  once to apply them. Updating bioAF itself never changes cloud resources.

### Upgrade notes

- The Google Cloud ingest bucket now exposes the `Range` header to browsers (for chunked uploads); it is
  applied by Check for Infrastructure Updates. Until then, uploads still work.
- New infrastructure buckets carry a `storage_uid` label (GCP) or tag (AWS) and a `bioaf_marker` that bioAF
  records to tell each bucket apart from one deleted and recreated under the same name. Existing buckets
  get both only when an administrator applies Check for Infrastructure Updates.
- Storage deploys, infrastructure updates and storage destroys are refused while a confirmed bucket has
  been replaced by another bucket with the same name, or cannot be checked, so an update never copies the
  confirmed bucket's marker onto a replacement.
- On installations deployed before this release, the storage buckets may show **Not confirmed**. Uploads,
  downloads, pipelines and the Plot Archive keep working; an administrator confirms the buckets under
  Infrastructure > Components before Destroy Storage is available. On AWS, run Check for Infrastructure
  Updates first so the buckets carry bioAF's marker, then confirm them.
- No new cloud permissions are needed. On AWS, bioAF checks a bucket's owner against the account ID the
  installer recorded, using the bucket permissions it already has, and no longer uses AWS's bucket creation
  date (which can change after a bucket policy edit).
- Uploads that were waiting in memory for completion when the update restarted the backend cannot be
  completed; upload those files again.
- Upload links issued before this release count as running uploads until an hour after they expire; then
  bioAF files any upload that arrived, and lists the rest for an administrator to release.

### Installation

- Setup on a new Google Cloud VM no longer stops at "Configure Cloud Logging": the Ops Agent's own default
  config, written when setup installs the agent, is kept aside as `config.yaml.package-default` and
  replaced by bioAF's. A config that existed before setup installed the agent is still left unchanged.
