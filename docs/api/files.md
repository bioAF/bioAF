# Files

Read-only metadata about files registered in bioAF. File bytes flow only
through the internal upload pipeline; this surface does not accept
uploads and does not return download URLs.

## Endpoints

| Method | Path | Scope |
| --- | --- | --- |
| `GET` | `/files` | `files:view` |
| `GET` | `/files/{id}` | `files:view` |

## GET /files

List file metadata in the calling SA's org.

Query params:

- `project_id` (optional).
- `experiment_id` (optional).
- `sample_id` (optional). Filters via the `sample_files` junction table.
- `source_type` (optional). Matches the `source_type` field on the file.
- `limit` (default 50, max 200).
- `cursor` (optional).

Response:

```json
{
  "items": [
    {
      "id": 555,
      "filename": "S001_R1.fastq.gz",
      "size_bytes": 123456789,
      "md5_checksum": "abcd...",
      "sha256_checksum": "ef01...",
      "file_type": "fastq",
      "source_type": "ingest",
      "project_id": 42,
      "experiment_id": 7,
      "sample_ids": [901, 902],
      "tags": {"chemistry": "v3.1"},
      "created_at": "2026-05-14T15:00:00Z",
      "external_id": "LIMS-FILE-9001",
      "project_external_id": "LIMS-PRJ-1",
      "experiment_external_id": "LIMS-EXP-042",
      "samples": [
        {"sample_id": 901, "external_id": "LIMS-SMP-1001", "experiment_id": 7, "experiment_external_id": "LIMS-EXP-042"},
        {"sample_id": 902, "external_id": null, "experiment_id": 7, "experiment_external_id": "LIMS-EXP-042"}
      ],
      "ready_at": "2026-05-14T15:02:11Z",
      "verification": {
        "status": "verified",
        "algorithm": "md5",
        "expected_checksum": "0123456789abcdef0123456789abcdef",
        "observed_checksum": "0123456789abcdef0123456789abcdef",
        "method": "streamed",
        "verified_at": "2026-05-14T15:02:11Z",
        "bytes_processed": 123456789,
        "total_bytes": 123456789,
        "error_code": null,
        "retryable": null
      }
    }
  ],
  "next_cursor": null
}
```

`gcs_uri` and similar storage-backend pointers are intentionally not
included in the public response.

`external_id` is the file's own identifier (or `null`); see [Conventions](conventions.md) for the
relationship fields. Reading file metadata never starts any checksum work.

## Readiness

`ready_at` is when bioAF first verified the file's stored bytes were available: the final object
exists with the expected size and, when an expected checksum was supplied, its MD5 matched. `null`
means readiness has not been established (yet, or ever, for files registered before this existed);
it does not by itself mean an older file is unusable. It records availability only, not scientific
QC or completeness, and is not reset when a file is later deleted.

## Checksum verification

`verification` is `null` when bioAF has no checksum information for the file. Otherwise:

| `status` | Meaning |
| --- | --- |
| `waiting_expectation` | A source that may list a checksum (a fetchngs samplesheet) has not been read yet |
| `not_required` | The source has no checksum; existence and size were enough |
| `queued` / `preparing` / `verifying` | Verification is waiting, making a protected NFS copy, or reading; `bytes_processed`/`total_bytes` show progress |
| `verified` | The stored bytes' MD5 matches the expected one |
| `checksum_mismatch` | They differ; both values are shown and the file stays unready |
| `failed` | Verification could not complete; `error_code` says why and `retryable` whether retrying may help without a fix |
| `unverified_legacy` | A checksum recorded before verification existed; it was never checked |

`expected_checksum` is what an uploader or source asserted; `observed_checksum` is what bioAF read
itself, from a trusted provider digest of that exact object (`method: provider`) or by reading the
bytes (`method: streamed`). A transient failure is retried automatically (60 s, 5 min, 30 min, 2 h)
before it becomes `failed`. Users with file edit permission can retry a failed or mismatched
verification from the file's details in bioAF after fixing the cause.

## GET /files/{id}

Returns one `FileOut` or `404 file_not_found` (also if the file belongs
to a different org).

## Discovering new files

Poll `GET /files` and deduplicate by file ID. Start from the first page to discover newly created rows;
a cursor moves toward older records, not toward records added after the previous poll.
For change notifications, subscribe to `file.registered` and `file.ready`: their payloads already carry
the file's identity and links, so a follow-up request is only needed for other metadata. See [Webhooks](webhooks.md) for event types and receiver setup.
