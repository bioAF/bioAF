# Release Notes

## v0.6.3

Infrastructure lifecycle stability, Cloud Logging, and deploy UX improvements.

### Cloud Logging

- Auto-detect GCE and attach Cloud Logging using the app's configured service account
- Install Ops Agent via `./bioaf start` for Docker container log collection
- Add `logging.logEntries.create` to GCP validation permission checks

### Infrastructure Lifecycle

- Replace 30-minute hard timeout with in-memory process registry so GKE deploys run to completion
- Fix lock file deletion to use app credentials instead of ADC
- Fix orphaned resource cleanup returning 404 for valid resources
- Expand orphan detection and cleanup to cover IAM service accounts
- Deduplicate orphaned resource entries across repeated failures
- Add GKE cluster and service account scanning via GKE/IAM APIs
- Persist tfvars on each TerraformRun for audit and reproducibility (migration 064)

### Deploy UX

- Show full planned resource list in deploy modal from the start (Queued/Setting up/Done states)
- Move teardown and storage destroy to background endpoints with polling
- Add region/zone selection at deploy time with cross-region cost warning
- Fix empty modal when no active run (idle state with "Starting operation...")
- Fix modal stuck after operation completes (terminal status persistence)
- Visible scrollbar on resource list

## v0.6.2

Audit log coverage gaps and activity feed event fixes.

### Audit Log Coverage (closes #153)

- Add logout endpoint (POST /api/auth/logout) with audit logging
- Log failed login attempts with reason (invalid credentials, account deactivated)
- Log file content serving as download audit entries
- Change role update audit action from generic "update" to "role_change" with old/new role names
- Log environment build success and failure from the build poller
- Log postgres and config backup completion and failure
- Log quota exceeded events alongside event bus emission
- Log notebook session access (who opened which session)
- Normalize download action name from "downloaded" to "download"
- Update audit log page filters with new entity types and actions
- Color-code new action badges (failures red, success green, warnings amber, role changes purple)

### Activity Feed Fixes

- Add PIPELINE_STARTED event type, emitted on successful run submission
- Emit PIPELINE_COMPLETED and PIPELINE_FAILED from pipeline monitor completion handler (event types existed but were never fired)
- Fix AUTO_RUN_BUDGET_DISABLED payload using wrong key ("organization_id" instead of "org_id"), silently dropped by NotificationRouter
- Fix AUTO_RUN_LAUNCHED payload missing org_id, user_id, and all display fields
- Frontend logout now calls backend endpoint so audit log entry is created

## v0.6.1

Pipeline run cost estimates based on actual GCP instance pricing.

### Cost Estimates

- Store cost estimate from compute adapter when launching a pipeline run (closes #203)
- Replace flat-fee stub with actual hourly spot rate for the pipeline node pool (n2-highmem-16)
- UI column renamed from "Cost" to "Est. $/hr" to clarify that values are hourly node rates, not totals

## v0.6.0

Automatic pipeline runs triggered by sample completeness, manifest reconciliation fixes, pipeline execution fixes, and UI cleanup.

### Auto-Run Pipelines

- Configure pipelines to run automatically when all expected files for a sample arrive and pass MD5 verification
- New ExperimentAutoRun and PendingAutoRun models with API endpoints for CRUD and status
- Background loop launches pending runs after configurable delay
- Auto-run evaluation integrated into the manifest ingest flow
- Replaced old trigger infrastructure (trigger_service, pipeline_triggers) with the new auto-run system

### Manifest Ingest Fixes

- Fix race condition where files arriving before the manifest were never linked to samples
- Retroactive reconciliation: when a manifest arrives, match already-ingested files by MD5 + filename + org + 2-hour time window
- Content-aware redelivery guard: compare incoming manifest entries against existing ones instead of just checking for existence
- Forward-path query now prefers MD5+filename match, falls back to filename-only for checksum mismatch detection
- Shared reconcile_manifest_entry() helper eliminates duplication between forward and retroactive paths

### Pipeline Execution Fixes

- Re-enable Fusion for GCS-backed pipeline runs (was incorrectly made opt-in, breaking all K8s process pods)
- Fix trace parser reading wrong column for process names ("process" vs "name" in Nextflow trace.tsv)
- Fix Nextflow K8s executor test to match Fusion-always-on behavior

### Pipeline Run UI

- Show pipeline logs directly without process dropdown for K8s runs (single log, no selection needed)
- Auto-detect protocol from sample chemistry_version, remove manual CV dropdowns from launch wizard
- Add bulk sample deletion with confirmation modal

### Navigation and Settings

- Remove unused Pipeline Scheduling placeholder page
- Move Naming Profiles from Settings to Data & Files section
- Consolidate GCP, SMTP, and Slack settings into Settings > Integrations with tabbed layout
- Add Seqera tab with coming-soon placeholder for Fusion license support

## v0.5.5

Auto-ingest pipeline hardening and manifest-driven file association. Groundwork for the upcoming auto-run pipeline feature.

### Auto-Ingest Fixes

- Pass stored GCP service account credentials through all downstream GCS operations (manifest reads, file copies, cleanup deletes)
- Fix double-delete: skip cleanup when move_file already deletes the source
- Fix duplicate manifest entries on Pub/Sub message redelivery
- Fix ManifestEntry reconciliation when duplicate pending entries exist
- Convert base64 MD5 from GCS Pub/Sub to hex for manifest checksum comparison
- Move manifest reconciliation before file copy so resolved experiment IDs determine the GCS prefix

### Manifest-Driven Sample Linkage

- Derive experiment and project from resolved samples in manifest ingest
- Create sample_files junction rows during file ingest for manifest-resolved samples
- Set file.experiment_id from manifest resolution so files appear in the correct experiment
- Add batch-position mapping via sample_index (S-number) segment in naming profiles

### UI

- Replace Sample Batch, Seq. Batch, and Pos. columns on the samples table with a Files count column
- Fix CSV upload custom field storage and mapping
- Fix auto-ingest settings save and listener restart behavior

### Housekeeping

- Rename sample_id_external to sample_id_unique across the codebase (DB column unchanged, additive-only)
- Fix file deletion blocked by manifest_entries FK constraint
- Fix serialize_entity to handle attribute/column name mismatches

## v0.5.4

Bug fix for database restore and UI cleanup on the Backup & Recovery page.

- Fix `_build_restore_url()` mangling database credentials when the PostgreSQL username contains "bioaf" (caused auth failures after restore swap)
- Replace browser `confirm()` dialogs with in-app ConfirmDialog on all backup restore/accept/reject actions

## v0.5.3

Setup wizard overhaul and installer improvements.

### Setup Wizard

- Setup flow now starts with a terminal-issued setup code that proves host access, replacing the old email verification step
- Wizard steps reordered: setup code, admin creation, org name, GCP credentials, SMTP, infrastructure decision, stack selection
- "Skip for now" buttons renamed to "Do this later" throughout
- Infrastructure step is a decision fork: deploy now or configure later
- Infrastructure init button shows processing state during terraform setup
- Removed team invite step from the wizard (available later from Settings)
- Price estimate removed from Kubernetes + GCS card

### CLI

- `./bioaf setup` now auto-runs the installer when `.env` or TLS certs are missing, so users can go from `git clone` to `./bioaf setup` in one step
- `./bioaf setup` prints the one-time setup code in green with the login URL
- macOS and Windows are detected early with a message pointing to the GCP setup docs
- `./bioaf create-admin` deprecated in favor of the web-based setup wizard

### Backend

- New `SetupCodeService` generates 6-character alphanumeric codes (bcrypt hashed, 1-hour TTL, single-use)
- New bootstrap endpoints: `generate-setup-code` and `verify-setup-code`
- `create-admin` endpoint now requires a setup JWT instead of being fully open
- Bootstrap status endpoint returns `has_setup_code` and `has_admin` fields
- Non-streaming `POST /api/v1/infrastructure/terraform/init` endpoint for the setup wizard
- Migration 061 adds `setup_code_hash` and `setup_code_expires_at` to organizations

### Getting Started (stubbed)

- 13-slide onboarding component with highlight overlays built but not yet linked
- Screenshots from marketing site included as placeholders, will be recaptured from the running app
- Route and component exist at `/getting-started` but are not accessible from the UI

## v0.5.2

Batch UX rework, custom fields, and entity snapshots.

### Batch UX

- Batches are now text fields on samples with find-or-create behavior instead of separate management pages with ID assignment
- Sample batches scoped per experiment, sequencing batches scoped per organization
- Batch codes added to sample field defaults at experiment creation
- CSV upload columns renamed to user-facing `sample_batch` and `sequencing_batch`
- Batches tab renamed from "Sample Batches" and counter removed

### Custom Fields

- Custom fields section on experiment create always visible (no longer gated behind template selection)
- Template-driven custom fields auto-populate; users can add arbitrary fields on top
- Custom fields support `is_required` flag with migration 059
- Custom fields editable on experiment detail page overview
- Experiment custom fields now inherited by samples as per-sample values (migration 060, new `sample_custom_fields` table)
- Sample create/edit forms render experiment custom field inputs
- Sample view modal displays custom field values

### Entity Snapshots

- Entity snapshots model and migration for point-in-time metadata capture
- Snapshot integration into audit service with optional snapshot parameter

### Manifest-Driven Ingest (foundation)

- Sequencing batch and manifest entry models with API
- Manifest parsing service for md5sum and CSV formats
- Manifest retry service for pending file verification
- Activity feed logging for manifest ingest events
- Sample completeness trigger and trigger_on schema field
- Auto-ingest manifest configuration UI
- This lays the groundwork for pipeline automation but does not finalize it

### Other

- Restored dropped columns on `sample_batches` (instrument model, platform, quality score encoding, sequencer run ID)
- GEO export reads instrument from sequencing batch
- Dropdown widths in field defaults now match text input widths

## v0.5.1

Improve notebook file selection UX.

- Files in the notebook launch picker are now sub-grouped by GCS subdirectory path (e.g., star/001/Gene/filtered vs star/001/Gene/raw), so identically named files from different pipeline stages are clearly distinguishable
- Each file shows a source type badge (Pipeline, Notebook, Upload) and creation date
- Files linked to a sample no longer duplicate under "Experiment Files"
- Launch and detail modals widened to 800px to prevent truncation

## v0.5.0

Notebook file lifecycle and environment build versioning.
This release introduces a complete file lifecycle for notebook and SSH sessions, fixing GCS storage mounting and adding structured input/output management with full provenance tracking.

### GCS Storage Fixes

- Fix GCS bucket mounting for notebook and SSH sessions: working bucket config, FUSE CSI annotation, SA key secret mount, and gcloud auth activation for Workload Identity environments
- Fix Workload Identity annotation not applied after namespace was cached
- Add gcs-sync sidecar container for reliable output persistence at shutdown

### Notebook File Lifecycle (ADR-040)

- Input files now mount with directory structure preserved: `/data/{project}/{experiment}/{sample}/{tool}/filename`
- Designated `/outputs/` directory on all session types (Jupyter, RStudio, SSH) for persistent analysis outputs
- On shutdown: outputs synced to GCS, notebook/script files (.ipynb, .Rmd, .R, .py) captured automatically
- Two-phase output persistence: working bucket during session, moved to results bucket on close
- Output files registered with full provenance (source_type=notebook_output, linked to project/experiment)
- 30-minute shutdown timeout for large file sync with UI status indicator
- Fix FILE_INVENTORY.md shell escaping that broke init container file copying partway through

### Environment Build Versioning (ADR-041)

- Rebuilding an environment version creates a minor version (v1 rebuild produces v1.1) instead of overwriting the image
- New `build_number` column on EnvironmentVersion with unique constraint
- Image tags use `v{version}.{build}` format (e.g., `v1.1`, `v1.2`)
- New rebuild endpoint: `POST /environments/{id}/versions/{vid}/rebuild`
- Notebook sessions now link to `environment_version_id` for traceability

### Provenance

- Session provenance endpoint: `GET /notebooks/sessions/{id}/provenance`
- Provenance reports for notebook outputs now include environment version, input files, session resources, and git info
- Markdown and PDF renderers display full source section for notebook and pipeline outputs
- Provenance preview panel displays inline source details instead of skipping nested data

### Frontend

- Shutdown sync indicator: spinner with "Syncing outputs to GCS..." while session stops
- Environment version picker shows `v{version}.{build}` format and passes `environment_version_id` in launch request
- Session detail modal shows environment version and provenance for stopped sessions
- Toggleable quick start guide on Notebooks and Work Nodes pages explaining `/data/`, `/outputs/`, environments, git, and credentials

### Schema Changes

- Migration 057: adds `build_number` to `environment_versions`, `gcs_output_prefix` to `compute_sessions`

## v0.4.1

Fix cellxgene adapter, image pipeline, and publish UX (#195)

## v0.4.0

Usability: real backups, service health, version checking (#194)
