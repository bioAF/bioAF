# ADR-036: Data Export and Download System

**Status:** Accepted
**Date:** 2026-03-24
**Deciders:** Brent (repository owner)

---

## Context

bioAF is preparing for its first real-world users. ADR-012 establishes that customers own all their data and can access it directly via GCS and PostgreSQL. However, "can access" is not the same as "can easily retrieve." A bench scientist who wants to leave the platform or back up their work should not need to learn `gsutil` or write SQL queries.

The platform currently supports:

- Single file download via signed GCS URL (`GET /api/files/{id}/download`)
- GEO metadata export for experiments and projects (ZIP with Excel + validation reports, no raw data files)

What is missing:

1. **No download button on the Files page.** The signed URL endpoint exists but the frontend does not expose it.
2. **No multi-file download.** Users cannot select several files and download them together.
3. **No experiment-level data export.** There is no way to download all files, metadata, and provenance for an experiment as a single package.
4. **No project-level data export.** Same gap at the project level, with the added need for experiment-based folder organization.

These gaps create a real risk: if a user decides to leave bioAF, the process of retrieving their data is manual, tedious, and error-prone. For a platform competing with Seven Bridges, this is unacceptable.

---

## Decision

Build a data export and download system with four capabilities, each building on the previous.

### 1. Single File Download Button

Add a download action to the Files page. When clicked, the frontend fetches the signed URL from `GET /api/files/{id}/download` and triggers a browser download. No backend changes required beyond audit logging.

### 2. Multi-File Download via Signed URLs

When a user selects multiple files on the Files page and clicks "Download Selected," the frontend triggers individual browser downloads for each selected file using signed URLs. This approach is chosen over server-side ZIP generation for the Files page because:

- No backend resource pressure (CPU, memory, temp storage)
- No timeout risk for large files
- Each file succeeds or fails independently
- The Files page is paginated at 25, bounding the selection set
- JavaScript can programmatically trigger multiple downloads without spamming browser prompts

### 3. Experiment Data Export

A new export action on the experiment detail page (and experiment list menu) that packages all data associated with an experiment into a structured download. The flow:

1. User clicks "Export Data" from the experiment menu
2. A modal presents options:
   - **Include FASTQ files** (checkbox, default unchecked) -- raw FASTQs can be very large
   - **Include provenance report** (checkbox, default checked)
3. The backend calculates the estimated total size and returns it to the modal
4. The modal presents two download options:
   - **Direct download** -- backend streams a ZIP to the browser
   - **Download from GCS** -- backend generates the ZIP, uploads it to a temp location in GCS, and returns a signed URL. The user downloads directly from GCS, which handles large files more reliably.
5. The user selects an option and the download begins

**Export package structure:**

```text
{experiment_name}/
  README.txt
  provenance_report.json
  provenance_report.md
  provenance_report.pdf
  sample_manifest.csv
  geo_export/
    geo_metadata_{experiment_name}.xlsx
    md5_checksums.txt
    validation_report.json
    validation_report.txt
    README.txt
  raw/
    {sample_id}_S1_L001_R1_001.fastq.gz   (if FASTQ option selected)
    {sample_id}_S1_L001_R2_001.fastq.gz
    ...
  results/
    qc_dashboard.html
    plots/
      ...
    {other pipeline outputs}
```

### 4. Project Data Export

Same concept as experiment export, applied at the project level. Organizes contents into sub-folders by experiment.

**Export package structure:**

```text
{project_name}/
  README.txt
  provenance_report.json
  provenance_report.md
  provenance_report.pdf
  sample_manifest.csv
  geo_superseries_export/
    {superseries GEO export contents}
  experiments/
    {experiment_1_name}/
      provenance_report.json
      provenance_report.md
      provenance_report.pdf
      sample_manifest.csv
      geo_export/
        {GEO export contents}
      raw/
        ...
      results/
        ...
    {experiment_2_name}/
      ...
```

### Size Estimation and Download Strategy

Before starting an export, the backend calculates the total size by summing `size_bytes` from all included file records. This is a database query, not a GCS operation, so it is fast.

The frontend displays the estimated size and offers both download options regardless of size, but recommends the GCS option for exports over 1 GB.

### Temporary ZIP Cleanup

When the "Download from GCS" option is used:

- The ZIP is uploaded to `{config_backups_bucket}/exports/{org_id}/{timestamp}_{export_type}.zip`
- The signed URL expires after 24 hours
- A background task runs hourly and deletes export ZIPs older than 24 hours
- The export record (metadata, not the ZIP) is retained in the audit log permanently

### API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/files/{id}/download` | GET | Existing -- returns signed URL for single file |
| `/api/experiments/{id}/export/estimate` | GET | Returns estimated export size in bytes |
| `/api/experiments/{id}/export/data` | POST | Streams ZIP or returns GCS signed URL |
| `/api/projects/{id}/export/estimate` | GET | Returns estimated export size in bytes |
| `/api/projects/{id}/export/data` | POST | Streams ZIP or returns GCS signed URL |

### Permissions

Download and export actions require the `files:download` permission. This permission is granted by default to the `admin` and `comp_bio` built-in roles. It can be added to custom roles.

### Audit Logging

All download and export actions are logged to the immutable audit trail (ADR-009):

- **Single file download:** file ID, filename, user, timestamp
- **Multi-file download:** list of file IDs, user, timestamp
- **Experiment export:** experiment ID, options selected (include FASTQs, include provenance), estimated size, download method (direct/GCS), user, timestamp
- **Project export:** project ID, experiment IDs included, options selected, estimated size, download method, user, timestamp

---

## Consequences

**Positive:**

- Users can retrieve all their data through the UI without needing GCS CLI tools or database access
- The signed-URL approach for multi-file download avoids backend resource pressure
- Size estimation and dual download options handle the full range from small metadata exports to multi-GB FASTQ bundles
- GEO export is embedded in the data export, so users get publication-ready metadata alongside their raw data
- Provenance reports travel with the data, ensuring reproducibility even after leaving bioAF
- Full audit trail for all download activity supports data governance and exfiltration detection

**Negative:**

- The GCS temp ZIP approach requires a cleanup background task and temporary storage costs
- Streaming large ZIPs directly to the browser can fail on unstable connections (mitigated by the GCS option)
- `weasyprint` is added as a backend dependency for PDF provenance reports (system-level dependencies: Cairo, Pango, GDK-PixBuf)

**Neutral:**

- The existing `GET /api/files/{id}/download` endpoint is unchanged; the frontend simply exposes it
- The existing GEO export service is reused, not duplicated, within the experiment/project export flow

---

## References

- ADR-012 (Customer owns all data and infrastructure state -- this ADR implements the "structured export" deferred in ADR-012)
- ADR-014 (GEO export service -- reused within experiment/project data exports)
- ADR-009 (Immutable audit log -- download events are audit-logged)
- ADR-022 (GCS storage backend -- temp ZIPs stored in GCS)
- ADR-032 (Custom RBAC -- `files:download` permission)
- ADR-037 (Provenance reporting -- provenance reports included in exports)
