# ADR-024: GCS Event-Driven Auto-Ingest

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

In production biotech workflows, data files arrive continuously from Contract Research Organizations (CROs), sequencing core facilities, and internal instruments. These files are delivered directly to cloud storage buckets: often automatically via SFTP-to-GCS bridges or direct GCS uploads by the CRO.

bioAF's original architecture required manual upload through the UI (drag-and-drop with experiment linking). This works for small-scale operations but breaks down when:

- A CRO delivers 50-100 files per batch, multiple batches per week
- A lab is running 500+ pipeline runs per day (as reported by practitioners)
- Multiple experiments produce data simultaneously
- The bench scientists generating data and the bioinformaticians consuming it work on different schedules

The desired state: files land in a GCS bucket, the system automatically picks them up, parses the filename to identify the project/experiment/sample (using naming profiles from ADR-023), catalogs them in the metadata database, and makes them visible across the platform. No human intervention required for the happy path.

---

## Decision

Implement a GCS event-driven auto-ingest system that monitors a dedicated ingest bucket for new files, automatically catalogs them, links them to bioAF entities, and triggers downstream pipeline evaluation (ADR-025).

### Event Pipeline

```text
GCS Ingest Bucket
       │
       │  Object finalize event
       ▼
  GCS Notification → Pub/Sub Topic
                          │
                          ▼
                   Pub/Sub Subscription
                          │
                          ▼
               bioAF Ingest Service (on GKE)
                    │
                    ├─ 1. Parse filename (ADR-023 naming profiles)
                    ├─ 2. Resolve entities (project, experiment, sample)
                    ├─ 3. Auto-create missing entities if needed
                    ├─ 4. Compute MD5 checksum
                    ├─ 5. Create file record in metadata DB
                    ├─ 6. Link file to resolved entities
                    ├─ 7. Copy file to bioaf-raw-{org} bucket
                    ├─ 8. Update experiment status if applicable
                    ├─ 9. Emit ingest event (for pipeline triggers, ADR-025)
                    └─ 10. Send notifications
```

### Entity Resolution and Auto-Creation

When the ingest service parses a filename and resolves entity codes:

**Project resolution:**

- If the project code maps to an existing bioAF project via the naming profile's `project_code_mappings` -> link the file to that project
- If the project code has no mapping -> auto-create a new project with the code as its name, status set to `unclaimed`, and a visual "Unclaimed" badge displayed everywhere the project appears

**Experiment resolution:**

- If the experiment code maps to an existing experiment -> link the file to that experiment
- If the experiment code has no mapping but the project exists -> auto-create the experiment under the resolved project, status set to `unclaimed`
- If neither project nor experiment exists -> auto-create the project first (unclaimed), then create the experiment under it (unclaimed), then link the file

**Sample resolution:**

- If a sample_id segment exists in the filename and matches an existing sample in the resolved experiment -> link the file to that sample
- If the sample_id doesn't match -> auto-create a minimal sample record with the external ID, status set to `unclaimed`
- If no sample_id segment exists in the naming profile -> file is linked to the experiment only

**Unclaimed entity behavior:**

- Unclaimed entities display a prominent visual badge throughout the platform (experiment list, project list, dataset browser, dashboard)
- Unclaimed entities have no pipeline settings, no metadata beyond what the filename provided, and no assigned owner
- Any user with appropriate permissions (comp_bio or admin) can "claim" an unclaimed entity, which opens a form to complete the metadata and assign ownership
- Unclaimed entities can receive additional files: the auto-ingest system continues linking new files to them as they arrive

### Bulk Reassignment

Files, samples, and experiments that were auto-created or auto-linked incorrectly (due to CRO typos, naming convention changes, etc.) can be reassigned:

- **Individual reassignment:** From any file detail page, change the linked project, experiment, or sample
- **Bulk reassignment:** From the dataset browser or experiment detail page, multi-select files or samples and reassign to a different experiment or project in one action
- All reassignments are recorded in the audit log with the original and new linkage

### File Processing Pipeline

On each ingested file:

1. **Duplicate detection:** Check MD5 checksum against existing files. If duplicate, flag but do not create a new record. Notify the user.
2. **File type detection:** Determine file type from extension and (where applicable) magic bytes. Categorize as: FASTQ, BAM, h5ad, count matrix, image, document, or other.
3. **Metadata extraction:** For known file types, extract relevant metadata (e.g., read count from FASTQ headers, cell/gene counts from h5ad).
4. **Checksum computation:** MD5 computed on the ingested file and stored in the file record.
5. **Copy to permanent storage:** File is copied from `bioaf-ingest-{org}` to `bioaf-raw-{org}` with a structured path: `{project_code}/{experiment_code}/{filename}`.
6. **Ingest bucket cleanup:** Original file in the ingest bucket is deleted after successful copy and cataloging. Configurable retention (default: delete immediately after copy; option to retain for N days).

### Ingest Status Tracking

Each ingested file has an ingest status visible in the UI:

| Status | Description |
|---|---|
| `ingesting` | File detected, processing in progress |
| `cataloged` | Successfully parsed, linked, and copied to permanent storage |
| `unmatched` | No naming profile matched; file cataloged with raw filename, awaiting manual review |
| `duplicate` | MD5 matches an existing file; flagged for review |
| `failed` | Processing error; file retained in ingest bucket for retry |

### Database Schema Additions

```sql
-- Ingest events log
ingest_events (
  id UUID PRIMARY KEY,
  file_id UUID REFERENCES files(id),
  source_bucket VARCHAR(255) NOT NULL,
  source_path VARCHAR(1024) NOT NULL,
  naming_profile_id UUID REFERENCES naming_profiles(id),
  parsed_project_code VARCHAR(255),
  parsed_experiment_code VARCHAR(255),
  parsed_sample_id VARCHAR(255),
  resolved_project_id UUID REFERENCES projects(id),
  resolved_experiment_id UUID REFERENCES experiments(id),
  resolved_sample_id UUID REFERENCES samples(id),
  auto_created_entities JSONB,  -- list of entities that were auto-created
  ingest_status VARCHAR(20) NOT NULL,
  error_message TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
)
```

### Terraform Resources

```hcl
# GCS notification -> Pub/Sub
resource "google_storage_notification" "ingest_notification" {
  bucket         = google_storage_bucket.ingest.name
  payload_format = "JSON_API_V1"
  topic          = google_pubsub_topic.ingest_events.id
  event_types    = ["OBJECT_FINALIZE"]
}

resource "google_pubsub_topic" "ingest_events" {
  name = "bioaf-ingest-events"
}

resource "google_pubsub_subscription" "ingest_worker" {
  name  = "bioaf-ingest-worker"
  topic = google_pubsub_topic.ingest_events.id

  ack_deadline_seconds = 600  # 10 min for large file processing

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.ingest_dead_letter.id
    max_delivery_attempts = 5
  }
}
```

### Notifications

The ingest system emits notifications for:

| Event | Recipients | Severity |
|---|---|---|
| Files successfully ingested and linked | Experiment owner, project members | Info |
| Unclaimed entity auto-created | All admin and comp_bio users | Warning |
| Unmatched file (no profile match) | All admin and comp_bio users | Warning |
| Duplicate file detected | File uploader (if known), experiment owner | Info |
| Ingest failure | Admins | Warning |
| Batch ingest complete (N files) | Experiment owner, project members | Info |

### Manual Upload Preserved

The existing drag-and-drop upload UI (F-010) remains fully functional. Manual uploads bypass the naming profile parser: users explicitly select the experiment and sample during upload. Both paths (auto-ingest and manual upload) produce identical file records in the metadata database.

---

## Consequences

**Positive:**

- CRO deliveries are automatically cataloged without human intervention
- Files are immediately visible across the platform (experiments, projects, samples, dataset browser)
- The auto-creation pattern ensures no data is lost even when metadata is incomplete
- Pub/Sub provides reliable, ordered event delivery with retry and dead-letter handling
- The ingest bucket separation keeps the landing zone clean and distinct from permanent storage

**Negative:**

- Auto-created entities with incomplete metadata may accumulate if not regularly claimed
- Pub/Sub adds a GCP service dependency and small cost (~$0.40 per million messages)
- Large file processing (multi-GB FASTQs) may hit the Pub/Sub ack deadline; requires careful timeout management
- The system trusts filename parsing for entity linkage: errors in CRO naming propagate automatically

**Neutral:**

- Manual upload is unaffected: this is additive functionality
- The ingest service runs as a deployment on GKE, scaling with the existing cluster

---

## References

- ADR-023 (configurable CRO naming profiles: provides the parser)
- ADR-022 (GCS storage backend: defines bucket structure including ingest bucket)
- ADR-025 (automated pipeline triggering: subscribes to ingest events)
- ADR-009 (immutable audit log: all ingest actions logged)
