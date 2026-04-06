# Release Notes

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
