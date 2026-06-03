### Notebooks + Work Nodes

- Backend now classifies failed sessions with a `failure_reason` + human-readable
  `failure_message`, so the UI can distinguish "Resource Failure" (GCE zone
  exhausted, image pull failed, OOM, quota exceeded) from a generic "Failed".
  Sessions also carry a `requested_disk_gb` field set at launch from the pool
  or VM boot disk. Notebook + work-node responses now expose the
  associated `project` (in addition to the existing `experiment` for notebooks)
  so the table can render a single "Linked to" column.
- The session-list endpoints accept a `bucket` query parameter:
  - `bucket=active` returns only non-terminal sessions (pending, starting,
    running, idle, stopping).
  - `bucket=recent` returns sessions created in the last 24h, any status.
  - `bucket=all` (or omitted) returns everything.
  Unknown bucket values are rejected with HTTP 400.
