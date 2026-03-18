# ADR-029: Signed URL Direct Upload for File Ingestion

**Status:** Accepted
**Date:** 2026-03-15
**Deciders:** Brent (repository owner)

---

## Context

bioAF's original file upload path proxied every byte through the backend: browser to nginx to FastAPI to GCS. This works for small files, but bioinformatics workloads regularly involve multi-gigabyte FASTQs (typical: 3-6 GB, max: 50-100 GB per lane). Proxying files of that size through the backend causes three problems:

1. **Memory pressure.** The backend must buffer or stream the entire file, consuming RAM proportional to file size. A 50 GB FASTQ upload can OOM the backend container (#99).
2. **Double network transit.** Every byte crosses the network twice (browser-to-backend, backend-to-GCS), doubling upload time and egress cost.
3. **No progress feedback.** The `fetch()` API does not expose upload progress on streaming requests. Users uploading large files see no indication of progress until the upload completes or times out.

GCS supports V4 signed URLs that grant time-limited, scoped permission for a browser to PUT an object directly into a bucket. The browser never authenticates to GCS itself; the signed URL embeds the authorization. This eliminates backend buffering, halves network transit, and enables XHR-based progress tracking.

---

## Decision

Replace the proxy upload path with a three-step signed URL flow for all user-initiated file uploads. The legacy `simple_upload` endpoint is retained for programmatic/internal use (CSV imports, small utility files) but is no longer the primary upload path.

### Upload Flow

```text
Browser                        Backend (FastAPI)                 GCS Ingest Bucket
  |                                  |                                  |
  |  1. POST /upload/initiate        |                                  |
  |  {filename, size, experiment_id} |                                  |
  |--------------------------------->|                                  |
  |                                  |  generate signed PUT URL         |
  |                                  |  (1-hour expiry, single object)  |
  |  {upload_id, signed_url}         |                                  |
  |<---------------------------------|                                  |
  |                                  |                                  |
  |  2. PUT file bytes (XHR)         |                                  |
  |------------------------------------------------------------------>  |
  |        progress events           |                                  |
  |  <upload.onprogress>             |                                  |
  |                                  |                                  |
  |  3. POST /upload/complete        |                                  |
  |  {upload_id}                     |                                  |
  |--------------------------------->|                                  |
  |                                  |  create file record              |
  |                                  |  link to experiment/samples      |
  |                                  |  emit DATA_UPLOADED event        |
  |                                  |  auto-transition experiment      |
  |  {FileResponse}                  |                                  |
  |<---------------------------------|                                  |
```

**Step 1 -- Initiate.** The frontend POSTs file metadata (filename, expected size, optional experiment_id and sample_ids) to `/api/files/upload/initiate`. The backend generates a V4 signed URL scoped to a single PUT on the ingest bucket at `uploads/{upload_id}/{filename}`, valid for one hour. No file bytes touch the backend.

**Step 2 -- Direct upload.** The frontend uses `XMLHttpRequest` (not `fetch()`) to PUT the raw file bytes to the signed URL. XHR exposes `upload.onprogress` events, enabling per-file progress bars. The Content-Type is `application/octet-stream`. The backend is not involved in this step.

**Step 3 -- Complete.** After the PUT succeeds, the frontend POSTs the `upload_id` to `/api/files/upload/complete`. The backend creates the file record in PostgreSQL, links it to the experiment and samples, detects file type from the extension, parses Illumina filename conventions for metadata extraction, emits a `DATA_UPLOADED` event, and auto-transitions the experiment status through intermediate states up to `fastq_uploaded`.

### CORS Configuration

The GCS ingest bucket must allow browser-originated PUT requests. The Terraform storage module configures:

- **Origins:** `["*"]` (the backend generates bucket-scoped, time-limited URLs, so origin restriction adds no security)
- **Methods:** `PUT`, `OPTIONS`
- **Response headers:** `Content-Type`, `Content-Length`, `Authorization`, `x-goog-*`
- **Max age:** 3600 seconds (matches signed URL expiry)

### Signed URL Generation

Signed URLs require credentials with signing capability. The backend reads `gcp_credential_source` from `platform_config`:

- **`service_account_key`**: Uses the stored JSON key to sign. This is the expected production path.
- **`vm_default`**: Falls back to Application Default Credentials. Works for GCE/GKE VMs with the `iam.serviceAccounts.signBlob` permission, but cannot sign if running outside GCP (e.g., local development without a key).

### Upload State

Pending uploads are tracked in an in-memory dict keyed by `upload_id`. An upload_id is consumed on completion and cannot be reused. This is sufficient for the current single-backend deployment; a future HA deployment would move pending state to Redis or a database table.

### MD5 Validation

`expected_md5` is optional at initiation. Computing MD5 client-side for multi-gigabyte files is impractical (minutes of CPU time in the browser), so the default is to skip client-side checksumming. GCS computes and stores CRC32C and MD5 server-side on upload; the backend can verify against GCS metadata if needed.

---

## Consequences

**Positive:**

- Backend never buffers file bytes, eliminating OOM risk for large uploads
- Upload speed limited only by the user's connection to GCS, not by backend throughput
- Real progress bars via XHR `onprogress` events
- Signed URLs are scoped to a single object and expire in one hour, limiting blast radius
- No change to the file data model or downstream pipeline integration

**Negative:**

- Two extra HTTP round-trips per upload (initiate + complete) compared to single-request proxy
- Pending upload state is in-memory; server restart orphans in-progress uploads
- CORS `origin: *` is broad (mitigated by URL scoping and expiry)
- Requires service account key for signing outside GCP environments

---

## References

- #100 -- Switch FASTQ uploads to signed URL flow for arbitrary file sizes
- #99 -- OOM crash: simple_upload buffers entire file in memory
- #97 -- simple_upload ignores experiment_id; GCS errors silently swallowed
- ADR-022 -- GCS as recommended storage backend
- ADR-024 -- GCS event-driven auto-ingest
- [GCS V4 signed URLs](https://cloud.google.com/storage/docs/access-control/signed-urls)
