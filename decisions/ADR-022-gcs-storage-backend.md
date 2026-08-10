# ADR-022: GCS as Recommended Storage Backend

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

bioAF's original architecture specified Google Filestore (NFS) as shared storage for compute nodes, notebook servers, and pipeline working directories. Filestore provides a POSIX filesystem that biologists use with standard file paths: no special tooling needed. However, Filestore has significant drawbacks:

1. **Cost:** Filestore's minimum instance is 1TB at ~$200/month (HDD) or ~$600/month (SSD). This is the single largest idle cost in the optional component stack.
2. **Inflexibility:** You pay for provisioned capacity, not actual usage. A team storing 50GB of working data still pays for 1TB.
3. **Scaling friction:** Increasing Filestore capacity requires a resize operation; decreasing is not supported without recreating the instance.

GCS buckets, by contrast, charge only for stored data (~$0.02/GB/month for Standard class), scale infinitely, and are already used by bioAF for raw data, results, and config backups. The tradeoff is that GCS is an object store, not a POSIX filesystem: pipeline containers cannot use standard file paths without an intermediary.

Feedback from practitioners confirmed that the POSIX filesystem convenience is nice but not essential. Their preferred pattern: download input files from GCS to the pipeline container's local storage, run the analysis, upload results to a different GCS bucket. This is how most cloud-native data platforms work.

---

## Decision

Implement GCS as the recommended and default storage backend for bioAF, using the BAL storage provider interface defined in ADR-020. NFS Filestore will be listed as an alternative option in the setup wizard but stubbed as "coming soon" until a future phase delivers the NFS adapter.

### Data Flow Pattern

```text
GCS Ingest Bucket          Pipeline Container              GCS Results Bucket
(raw data lands here)      (ephemeral, runs on K8s)        (permanent outputs)
       │                          │                               ▲
       │  1. Download inputs      │  2. Run analysis              │  3. Upload outputs
       └─────────────────────────►│──────────────────────────────►│
                                  │                               │
                              Local SSD /                    gsutil / GCS
                              emptyDir volume                client library
```

**Step 1: Stage inputs:** Before a pipeline job starts, an init container (or the pipeline's own startup logic) downloads the required input files from GCS to a local volume (Kubernetes `emptyDir` backed by node SSD). The BAL storage adapter's `stage_inputs()` method generates the download commands based on the file records in the metadata database.

**Step 2: Run analysis:** The pipeline processes files using standard local file paths. From the pipeline's perspective, files are in `/data/inputs/` and outputs go to `/data/outputs/`. No special GCS tooling needed inside the pipeline code.

**Step 3: Collect outputs:** After the pipeline completes, a sidecar container (or the pipeline's own cleanup logic) uploads output files from `/data/outputs/` to the results GCS bucket. The BAL storage adapter's `collect_outputs()` method handles the upload, registers files in the metadata database, and computes checksums.

### Bucket Structure

| Bucket | Purpose | Lifecycle |
|---|---|---|
| `bioaf-ingest-{org}` | Landing zone for incoming data (CRO deliveries, uploads). Monitored by auto-ingest (ADR-024). | Files moved to raw after cataloging |
| `bioaf-raw-{org}` | Permanent storage for raw input files (FASTQs, etc.) | Standard -> Nearline after 90 days (configurable) |
| `bioaf-working-{org}` | Intermediate pipeline outputs (BAMs, count matrices) | TTL configurable, default 30 days |
| `bioaf-results-{org}` | Final outputs (h5ad, figures, reports) | No expiration |
| `bioaf-config-backups-{org}` | Platform config snapshots | Tiered retention per ADR-004 |

The addition of `bioaf-ingest-{org}` is new: it serves as the monitored landing zone for the auto-ingest system (ADR-024). Files are cataloged on arrival, then copied to `bioaf-raw-{org}` for permanent storage. The original file in the ingest bucket is deleted after successful cataloging and copy (configurable retention).

### Notebook Storage

Interactive notebook sessions (Jupyter, RStudio) need persistent storage for home directories and working files between sessions. Two approaches, depending on session lifecycle:

**Option A: GCS-backed persistence (recommended):** Each user's notebook home directory is backed by a dedicated GCS prefix (`gs://bioaf-working-{org}/notebooks/{user_id}/`). On session start, the working directory is synced from GCS to local storage. On session end (or periodically during the session), changes are synced back. This eliminates persistent volume costs entirely.

**Option B: Persistent Volume Claims:** For users who need guaranteed POSIX semantics during long-running sessions, a Kubernetes PersistentVolumeClaim (backed by GCE Persistent Disk) can be provisioned per user. This costs ~$0.04/GB/month: far cheaper than Filestore but not free.

The default is Option A. Option B is available as a per-user configuration.

### Pipeline Path Resolution

The BAL storage adapter translates between bioAF's logical file references and the actual paths used by pipeline containers:

```python
# GCS storage adapter - resolve_input_path
def resolve_input_path(self, file_record: FileRecord) -> str:
    # For K8s pipelines, return the local path after staging
    return f"/data/inputs/{file_record.filename}"

def stage_inputs(self, file_records: list[FileRecord], working_dir: str) -> list[str]:
    # Generate gsutil commands to download files to the container
    paths = []
    for record in file_records:
        local_path = f"{working_dir}/{record.filename}"
        # Download from GCS to local volume
        gcs_client.download(record.gcs_uri, local_path)
        paths.append(local_path)
    return paths
```

Nextflow's native GCS support can also be used directly: Nextflow can read from and write to `gs://` URIs without staging. The adapter supports both patterns, configurable per pipeline.

### Terraform Modules

```hcl
# storage-gcs.tf
resource "google_storage_bucket" "ingest" {
  count    = var.storage_backend == "gcs" ? 1 : 0
  name     = "bioaf-ingest-${var.org_slug}"
  location = var.region

  uniform_bucket_level_access = true
  versioning { enabled = true }

  # Notification for auto-ingest (ADR-024)
  # Pub/Sub topic created in auto-ingest terraform
}

# Existing buckets (raw, working, results, config-backups)
# remain as-is: they are part of the mandatory foundation
```

### Cost Comparison

| Storage Scenario | Filestore (NFS) | GCS |
|---|---|---|
| Idle (no data) | ~$200/month (1TB minimum) | ~$0/month |
| 100GB raw data | ~$200/month (still 1TB minimum) | ~$2/month |
| 500GB raw + 200GB working | ~$200/month | ~$14/month |
| 2TB total | ~$400/month (resize required) | ~$40/month |

---

## Consequences

**Positive:**

- Eliminates the single largest optional component cost (Filestore ~$200/month minimum)
- Pay only for actual storage used, not provisioned capacity
- GCS scales infinitely without manual resize operations
- Object versioning and lifecycle policies already in place from the mandatory foundation
- Nextflow and Snakemake both have native GCS support
- The ingest bucket pattern enables clean separation of landing zone from permanent storage

**Negative:**

- Pipeline containers must explicitly stage inputs and collect outputs: adds latency for large files
- No POSIX filesystem semantics without an intermediary (gcsfuse has performance limitations for random I/O)
- Notebook home directory sync adds complexity compared to NFS mount
- Some legacy tools expect local filesystem paths; containerization mitigates this but doesn't eliminate it

**Neutral:**

- Raw data, results, and config backups were already in GCS: this decision extends GCS to working storage and notebook persistence
- The BAL abstraction (ADR-020) means pipeline definitions and UI components are unaffected by the storage backend choice

---

## References

- ADR-020 (BioAF Adapter Layer)
- ADR-004 (tiered backups: GCS bucket protections)
- ADR-024 (GCS event-driven auto-ingest: uses ingest bucket)
