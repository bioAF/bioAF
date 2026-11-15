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
      "created_at": "2026-05-14T15:00:00Z"
    }
  ],
  "next_cursor": null
}
```

`gcs_uri` and similar storage-backend pointers are intentionally not
included in the public response.

## GET /files/{id}

Returns one `FileOut` or `404 file_not_found` (also if the file belongs
to a different org).

## Discovering new files

Poll `GET /files` and deduplicate by file ID. Start from the first page to discover newly created rows;
a cursor moves toward older records, not toward records added after the previous poll.
For change notifications, subscribe to `file.registered` and `file.ready`, then retrieve the relevant
metadata through this API. See [Webhooks](webhooks.md) for event types and receiver setup.
