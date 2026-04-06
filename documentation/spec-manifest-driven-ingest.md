# Spec: Manifest-Driven Ingest with Batch Separation

**Status:** Draft (revised)
**Date:** 2026-04-05
**Revised:** 2026-04-05

---

## Overview

Experimental metadata flows through bioAF in a specific sequence: scientists create
experiments, register samples with metadata (including sample batch and sequencing batch
codes), then send samples to a CRO or sequencing core. The CRO ships back files to a GCS
bucket alongside a manifest file containing checksums.

Today, the `Batch` model conflates two distinct concepts: **sample batches** (samples
prepped together) and **sequencing batches** (samples sent together to a CRO/sequencing
core). These have a many-to-many relationship -- a sample batch can be split across
sequencing runs, and a sequencing run can contain samples from multiple sample batches
and even multiple experiments or projects.

This spec introduces:

1. Separation of sample batches and sequencing batches
2. Batch assignment as text code fields on samples (find-or-create)
3. Manifest-driven file ingest with checksum verification
4. Activity logging for ingest progress
5. Sample-completeness-aware pipeline triggers
6. Metadata snapshots for experimental provenance

---

## 1. Data Model Changes

### 1.1 Rename `Batch` to `SampleBatch`

The current `Batch` model represents sample preparation. Rename the table to
`sample_batches` and add a `sample_batch_id` column to `samples`. **Do not drop or rename
existing columns.** The original columns remain:

| Field | Type | Notes |
|-------|------|-------|
| id | int PK | |
| experiment_id | int FK | |
| name | str | Required |
| prep_date | date | Nullable |
| operator_user_id | int FK | Nullable |
| sequencer_run_id | str | Nullable (legacy, kept for data preservation) |
| instrument_model | str | Nullable (labs may track prep equipment here) |
| instrument_platform | str | Nullable |
| quality_score_encoding | str | Default "Phred+33" |
| notes | text | Nullable |
| created_at | datetime | |
| updated_at | datetime | |

**Important:** The four instrument/sequencer fields were originally added by ADR-013 when
batches conflated prep and sequencing. With the separation, these fields are primarily
relevant to sequencing batches (section 1.2). However, they must NOT be dropped from
sample_batches -- some labs use them to track prep equipment. All database changes must
be additive only.

### 1.2 New `SequencingBatch` Model

Represents a batch of samples sent to a CRO or sequencing core. Scoped per-organization
because a single sequencing batch can contain samples from multiple experiments and
projects.

| Field | Type | Notes |
|-------|------|-------|
| id | int PK | |
| organization_id | int FK | Required |
| code | str | Required, unique per org. The identifier the lab uses for the CRO shipment. |
| name | str | Nullable, user-facing label (e.g., "Run 2026-04-01") |
| status | str | "pending", "ingesting", "complete", "partial_complete", "failed" |
| instrument_model | str | Nullable |
| instrument_platform | str | Nullable |
| quality_score_encoding | str | Default "Phred+33" |
| sequencer_run_id | str | Nullable |
| manifest_received_at | datetime | Nullable, set when manifest parsed |
| expected_file_count | int | Nullable, set from manifest |
| ingested_file_count | int | Default 0 |
| notes | text | Nullable |
| created_at | datetime | |
| updated_at | datetime | |

**Relationships:**

- `organization` (Organization)
- `manifest_entries` (ManifestEntry, back_populates)
- `samples` (Sample, via sequencing_batch_id FK on Sample)
- `files` (File, via sequencing_batch_id FK on File)

### 1.3 New `ManifestEntry` Model

Each row represents one expected file from the manifest.

| Field | Type | Notes |
|-------|------|-------|
| id | int PK | |
| sequencing_batch_id | int FK | Required |
| expected_filename | str | Required, from manifest |
| expected_md5 | str | Required, from manifest |
| resolved_sample_id | int FK | Nullable, set after filename parsing |
| resolved_experiment_id | int FK | Nullable, set after filename parsing |
| resolved_project_id | int FK | Nullable, set after filename parsing |
| file_id | int FK | Nullable, set after file verified and ingested |
| status | str | "pending", "verified", "checksum_mismatch", "missing", "failed" |
| last_check_at | datetime | Nullable |
| retry_count | int | Default 0 |
| error_message | str | Nullable |
| created_at | datetime | |

### 1.4 Batch Assignment on Samples

Both batch types are assigned as text code fields when creating or editing a sample:

| Field on Sample | Type | Notes |
|-------|------|-------|
| sample_batch_code | str | Nullable. User types a code; backend find-or-creates a SampleBatch by code within the experiment. |
| sequencing_batch_code | str | Nullable. User types a code; backend find-or-creates a SequencingBatch by code within the organization. |

The backend resolves these to FK IDs (`sample_batch_id`, `sequencing_batch_id`) via
find-or-create:

- **SampleBatch**: scoped by `experiment_id`. Typing "prep-42" in experiment A and
  experiment B creates two separate SampleBatch records.
- **SequencingBatch**: scoped by `organization_id`. Typing "cro123" in any experiment
  finds or creates one shared SequencingBatch. A single sequencing batch can contain
  samples from different experiments and projects.

### 1.5 Update `File` Model

Add `sequencing_batch_id` nullable FK on `files`. Set when a manifest entry is verified
and the file is cataloged.

### 1.6 Manifest Configuration

Add to `IngestConfig` (platform_config keys):

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `manifest_filename` | str | "md5.txt" | Filename to watch for in ingest bucket |
| `manifest_format` | str | "md5sum" | Parser to use: "md5sum" or "csv" |
| `manifest_retry_interval_minutes` | int | 15 | How often to re-check missing files |
| `manifest_max_retries` | int | 48 | Max retries before marking file as failed (48 x 15min = 12 hours) |

These should be configurable via the existing Settings > Auto-Ingest UI.

### 1.7 New `EntitySnapshot` Model

Stores point-in-time metadata snapshots for provenance.

| Field | Type | Notes |
|-------|------|-------|
| id | bigint PK | |
| entity_type | str | "sample" or "experiment" |
| entity_id | int | |
| snapshot_json | JSONB | Full serialized state of the entity at this moment |
| audit_log_id | bigint FK | Links to the audit_log entry that triggered the snapshot |
| created_at | datetime | |

Indexed on `(entity_type, entity_id)`. Created automatically by `log_action()` when a
snapshot dict is provided. Only samples and experiments capture snapshots.

---

## 2. Manifest Parsing

### 2.1 Supported Formats

**md5sum format** (default):

The first line is a header comment containing the batch number. Remaining lines are
standard md5sum output. Example:

```text
# batch: SEQ-2026-0042
d41d8cd98f00b204e9800998ecf8427e  EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz
a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6  EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz
e5f6a7b8c9d0e1f2a3b4c5d6d41d8cd9  EXP015_SAMPLE0007_S7_L001_R1_001.fastq.gz
f6a7b8c9d0e1f2a3b4c5d6d41d8cd98f  EXP015_SAMPLE0007_S7_L001_R2_001.fastq.gz
```

**CSV format:**

```csv
batch_number,filename,md5
SEQ-2026-0042,EXP015_SAMPLE0003_S3_L001_R1_001.fastq.gz,d41d8cd98f00b204e9800998ecf8427e
SEQ-2026-0042,EXP015_SAMPLE0003_S3_L001_R2_001.fastq.gz,a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
```

### 2.2 Parse Flow

1. Read manifest content from GCS
2. Extract batch number (maps to SequencingBatch.code)
3. For each entry, extract filename and md5 checksum
4. Return structured result: `ManifestParseResult(batch_number, entries[])`

---

## 3. Manifest-Driven Ingest Flow

### 3.1 Trigger

When auto-ingest is enabled and a new file notification arrives from GCS Pub/Sub:

1. Check if the filename matches `manifest_filename` from config
2. If yes, enter manifest ingest flow (section 3.2)
3. If no, continue with existing single-file ingest flow

### 3.2 Manifest Ingest Flow

#### Step 1: Parse manifest

- Download manifest from ingest bucket
- Parse using configured format parser
- Extract batch code and file list

#### Step 2: Create or find SequencingBatch

- Look up existing SequencingBatch by `code` + `organization_id`
- If not found, create one with status "ingesting"
- If found and status is "complete", log a warning (duplicate manifest)

#### Step 3: Resolve samples from filenames

For each manifest entry:

- Parse the filename using active naming profiles (existing parser)
- Extract the sample name from the filename
- Look up the sample by `sample_id_external` in the organization
- This resolves the chain: sample name -> sample -> experiment -> project
- Create a `ManifestEntry` with resolved IDs and status "pending"
- Set `sample.sequencing_batch_id` if not already set

#### Step 4: Check files and verify

For each manifest entry with status "pending":

- Check if the file exists in the ingest bucket
- If exists, compute md5 and compare to expected
- If match: create File record, associate with sample and sequencing batch, copy to
  raw bucket, update ManifestEntry status to "verified", set `file_id`
- If mismatch: increment `retry_count`, update `last_check_at`, keep status "pending"
  (file may still be uploading)
- If not present: increment `retry_count`, update `last_check_at`, keep status "pending"

#### Step 5: Update batch status

After processing all entries:

- All verified -> status "complete"
- Some verified, retries remaining -> status "ingesting"
- Some verified, some exhausted retries -> status "partial_complete"
- All failed -> status "failed"

#### Step 6: Emit events

- `SEQUENCING_BATCH_DETECTED` -- when manifest first parsed
- `SEQUENCING_BATCH_FILE_VERIFIED` -- per file, includes sample/experiment/project info
- `SEQUENCING_BATCH_COMPLETE` -- when all files verified
- `SEQUENCING_BATCH_PARTIAL` -- when retries exhausted with some files missing

### 3.3 Retry Mechanism

A background task runs on `manifest_retry_interval_minutes`:

1. Query ManifestEntry records with status "pending" and
   `retry_count < manifest_max_retries`
2. For each, re-run Step 4 (check file, verify md5)
3. If `retry_count >= manifest_max_retries`, set status to "failed"
4. Update SequencingBatch status accordingly

---

## 4. Activity Logging

Surface ingest progress in the existing activity/notification system.

### 4.1 Log Messages

| Event | Message Format |
|-------|---------------|
| Manifest detected | "Sequencing batch {code} found. Ingesting {file_count} files..." |
| File verified | "Sequencing batch {code}: File {filename} associated with {project_name}, {experiment_name}, {sample_name}" |
| File retry | "Sequencing batch {code}: File {filename} not yet available, will retry ({retry_count}/{max_retries})" |
| File failed | "Sequencing batch {code}: File {filename} failed verification after {max_retries} attempts" |
| Batch complete | "Sequencing batch {code} complete. All {file_count} files ingested." |
| Batch partial | "Sequencing batch {code} partially complete. {ingested}/{total} files ingested, {failed} failed." |

### 4.2 Delivery

- In-app activity log (existing notification system)
- Audit trail entries for each file association (existing audit service)

---

## 5. Pipeline Trigger Enhancements

### 5.1 Sample Completeness Trigger

Extend the existing `event_driven` trigger mode with a new concept: **sample
completeness**.

Today, event-driven triggers fire on file type match with an optional batching window.
The enhancement adds the ability to trigger when all expected files for a sample within a
sequencing batch have been verified.

**New fields on `EventTriggerConfig`:**

| Field | Type | Notes |
|-------|------|-------|
| trigger_on | str | "file_upload" (existing behavior) or "sample_complete" |
| required_file_types | list[str] | For sample_complete: file types that must all be present. e.g. ["fastq_r1", "fastq_r2"] |

### 5.2 Sample Completeness Evaluation

When `trigger_on = "sample_complete"` and a `SEQUENCING_BATCH_FILE_VERIFIED` event fires:

1. Identify the sample from the event
2. Query all ManifestEntry records for this sample in this sequencing batch
3. Check if all entries with matching file types in `required_file_types` have status
   "verified"
4. If yes, the sample is complete -- evaluate the trigger
5. The trigger submits a pipeline run targeting the verified files for that sample

### 5.3 Example Flow

1. Auto-run rule on Experiment0015: "When new FASTQ files complete for a sample, run
   scrnaseq pipeline"
   - trigger_on: "sample_complete"
   - required_file_types: ["fastq_r1", "fastq_r2"]
2. Sequencing batch SEQ-0005 manifest arrives with 4 files for sample SAMPLE0003 (R1,
   R2) and sample SAMPLE0007 (R1, R2)
3. SAMPLE0003 R1 file verified -- check completeness: R2 not yet verified, skip
4. SAMPLE0003 R2 file verified -- check completeness: both R1 and R2 verified, trigger
   fires
5. Pipeline run created for SAMPLE0003 targeting R1 and R2 files
6. SAMPLE0007 follows the same pattern independently

---

## 6. Frontend: Batches Tab

### 6.1 Layout

The batches tab in the experiment detail page shows both batch types:

**Sample Batches** (top/left section):

- Read-only list of sample batches for this experiment (created implicitly when samples
  are created with a sample_batch_code)
- Each card shows: code, prep_date, operator, sample count, notes
- Click to expand and see assigned samples

**Sequencing Batches** (bottom/right section):

- List of sequencing batches that contain samples from this experiment
- Includes a "Create Sequencing Batch" button for manual creation (when a user wants
  to set up a batch before samples are assigned to it)
- Each card shows: code, status badge, instrument_model, file progress
  (ingested/expected), received date
- Status badge colors: pending (gray), ingesting (blue/animated), complete (green),
  partial_complete (yellow), failed (red)
- Click to expand and see:
  - List of manifest entries with status icons
  - Which samples received files
  - Any failed/pending files

### 6.2 Sample Forms

Both the sample create form and edit form include text inputs for:

- **Sample Batch** -- optional text field, user types a code
- **Sequencing Batch** -- optional text field, user types a code

These also appear as supported columns in CSV upload (`sample_batch` and
`sequencing_batch` column headers).

### 6.3 Responsive Behavior

- Desktop (>1024px): side-by-side columns
- Tablet/mobile: stacked, sample batches first

---

## 7. API Changes

### 7.1 Sample Create/Update

`POST /api/experiments/{id}/samples` and `PATCH /api/samples/{id}` accept:

- `sample_batch_code` (str, optional) -- find-or-create SampleBatch by code + experiment
- `sequencing_batch_code` (str, optional) -- find-or-create SequencingBatch by code + org

The response includes both batch objects:

```json
{
  "sample_batch": { "id": 1, "code": "prep-42" },
  "sequencing_batch": { "id": 5, "code": "cro123" }
}
```

### 7.2 Renamed Endpoints

| Old | New |
|-----|-----|
| `GET /api/batches/{id}` | `GET /api/sample-batches/{id}` |
| `PATCH /api/batches/{id}` | `PATCH /api/sample-batches/{id}` |
| `POST /api/batches/{id}/assign-samples` | `POST /api/sample-batches/{id}/assign-samples` |
| `GET /api/experiments/{id}/batches` | `GET /api/experiments/{id}/sample-batches` |
| `POST /api/experiments/{id}/batches` | `POST /api/experiments/{id}/sample-batches` |

### 7.3 New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sequencing-batches` | List sequencing batches for org |
| GET | `/api/sequencing-batches/{id}` | Get sequencing batch detail with manifest entries |
| GET | `/api/experiments/{id}/sequencing-batches` | List sequencing batches containing samples from this experiment |
| POST | `/api/sequencing-batches` | Create sequencing batch manually |
| PATCH | `/api/sequencing-batches/{id}` | Update sequencing batch metadata |

### 7.4 Updated Settings Endpoints

Extend `POST /api/v1/settings/auto-ingest` to accept:

- `manifest_filename` (str)
- `manifest_format` (str)
- `manifest_retry_interval_minutes` (int)
- `manifest_max_retries` (int)

Extend `GET /api/v1/settings/auto-ingest` to return these fields.

---

## 8. Migration Strategy

**All migrations must be additive only.** Never drop columns, rename columns, or drop
tables.

- Add new columns alongside existing ones (e.g., `sample_batch_id` alongside `batch_id`)
- Create new tables (sequencing_batches, manifest_entries, entity_snapshots)
- Add new FK columns (sequencing_batch_id on samples and files)
- Leave legacy columns in place; deprecate in code by not writing to them
