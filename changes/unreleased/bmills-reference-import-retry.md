### Reference data

- The URL importer now resumes a dropped download via HTTP
  `Range: bytes=<N>-` instead of starting over from byte 0. When the
  source CDN closes the TCP connection mid-stream (a 10x Genomics
  11.4 GB reference died at 7.1 GB with `peer closed connection without
  sending complete message body`), the importer reissues the GET with a
  Range header pointing at the next byte it needs, the server returns
  `206 Partial Content`, and only the remaining bytes are re-fetched.
  If the server doesn't honor the Range request (returns `200 OK` with
  the full body), the importer falls back to a clean restart so the
  final object is still correct. Up to 3 attempts with 5s / 10s / 20s
  exponential backoff.

- To support resume cleanly across all extract modes
  (`none` / `gzip` / `tar` / `tar.gz`), the source URL is staged to a
  local tempfile during the download phase and only uploaded to GCS
  once the download finishes (and the optional `.md5` verification
  passes). This requires `<size of largest reference>` of free space
  on the backend container's `/tmp`. On a 10 GB import that's a
  one-time scratch cost; the tempfile is deleted as soon as the upload
  completes (or the import fails).

- MD5 verification now happens before any bytes hit GCS. Previously the
  importer uploaded as it streamed and deleted the blob on mismatch;
  now a mismatch means no blob is ever created, so a failed MD5 leaves
  the references bucket clean by construction.

- The URL-import path now finalizes the dataset row after the worker
  finishes. Previously `ReferenceImportProgress` reached `active` but
  `ReferenceDataset.status` stayed at `uploading` forever, so the UI's
  `Importing` badge persisted even on a completed 10.66 GB import. The
  service now writes a `ReferenceDatasetFile` per imported file,
  aggregates `total_size_bytes` / `file_count` / `md5_manifest_json`,
  flips `dataset.status` to `pending_approval` for public datasets or
  `active` for internal (matching the upload-flow rule), and writes an
  `import_completed` audit entry.
