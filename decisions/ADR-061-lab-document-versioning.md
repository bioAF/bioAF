# ADR-061: Upload-New-Version Versioning Model for Lab Documents

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

Lab Documents (PDFs, Word docs, spreadsheets) need version history so previous versions are
preserved when documents are updated. Inline editing is an alternative but is only practical for
text formats and adds significant implementation complexity.

The platform already has a file-ingest path. User-initiated uploads use the signed-URL direct-to-GCS
flow (ADR-029): `UploadService.initiate_upload(...)` returns a V4 signed PUT URL and a target
`gcs_uri`, the browser PUTs bytes directly to GCS, then `UploadService.complete_upload(...)`
finalizes. `GcsStorageService` (`backend/app/services/gcs_storage.py`) wraps GCS reads/writes/moves,
and bucket names (including `working_bucket_name`) are resolved from the global `platform_config`
table, not per-organization.

## Decision

Version history is maintained through explicit "Upload New Version" actions. Inline editing is
not supported in v1.

Data model:

- `lab_documents` holds current-version metadata (`current_version`, `gcs_uri`, `file_name`,
  `file_size_bytes`, `mime_type`, `md5_checksum`, `is_archived`).
- `lab_document_versions` holds one append-only row per version
  (`UNIQUE(document_id, version_number)`) with its own `gcs_uri`, checksum, size, and optional
  `change_note`.

Storage and upload:

- Files live in the existing working bucket under a Lab Knowledge prefix:
  `gs://{working_bucket}/lab-knowledge/documents/{document_id}/v{n}/{file_name}`. Paths are
  constructed inline (consistent with `GcsStorageService.build_experiment_prefix` style); no new
  path-construction utility is introduced.
- Uploads reuse the signed-URL flow from ADR-029 rather than streaming bytes through the API.
  **Checksum reconciliation:** the spec asked for a server-side MD5, but in the signed-URL flow
  the API never sees the bytes. Instead, on `complete`, the server reads the GCS object's
  server-computed `md5Hash` metadata via `GcsStorageService` and stores it in
  `lab_document_versions.md5_checksum` and `lab_documents.md5_checksum`. This preserves the
  "checksum stored and verifiable" guarantee (AC-A10) without proxying large files. (The legacy
  `UploadService.simple_upload` server-streaming path remains available for programmatic/CLI use.)
- Uploading a new version writes a new `lab_document_versions` row with `version_number = current + 1`,
  stores the file at the `v{n+1}` path, and updates `lab_documents.current_version` and `gcs_uri`
  to point at it. Previous versions remain downloadable.

Every upload, new-version, metadata edit, and archive writes to the audit log via
`audit_service.log_action` with `entity_type="lab_document"`.

## Rationale

Upload-new-version is simple, predictable, and works for all file types (PDF, DOCX, XLSX), which
inline editing cannot. Reusing the ADR-029 signed-URL flow avoids proxying multi-gigabyte files
through the API and reuses already-hardened upload code. Reading GCS's own `md5Hash` is the
correct way to obtain a trustworthy checksum in a flow where the server never holds the bytes,
and it matches how GCS already exposes integrity metadata.

## Consequences

**Positive:**

- Simple, predictable model that works for every file type.
- Previous versions are always accessible for download.
- Explicit upload intent reduces accidental overwrites.
- Reuses the existing, hardened signed-URL upload path and GCS adapter.

**Negative:**

- No inline editing for text/markdown documents in v1; users download, edit, and re-upload.
- Checksum is sourced from GCS metadata rather than computed by the API, so it attests to what
  GCS received rather than being independently recomputed by the app. This is an acceptable
  trust boundary given GCS is the system of record for the bytes.
