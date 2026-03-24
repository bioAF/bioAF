# ADR-037: Provenance Reporting System

**Status:** Accepted
**Date:** 2026-03-24
**Deciders:** Brent (repository owner)

---

## Context

bioAF competes with Seven Bridges and similar platforms where provenance tracking is a core differentiator. The platform already captures substantial provenance data -- pipeline run parameters, container versions, Nextflow traces, file checksums, sample metadata, audit logs, and a provenance DAG visualization. But this data is scattered across database tables and API responses. There is no way to generate a portable, self-contained provenance report that a user can take with them.

For regulatory compliance, publication reproducibility, and data portability (ADR-012), users need to produce provenance reports that answer: "For this artifact, what exact inputs, tools, parameters, and environment produced it, and who did what along the way?"

The platform must generate these reports at five levels:

1. **Project** -- full lineage across all experiments
2. **Experiment** -- all samples, pipeline runs, and outputs for one experiment
3. **Sample** -- lifecycle from registration through processing
4. **Pipeline run** -- complete execution record with inputs, outputs, parameters, environment
5. **Artifact (file)** -- trace back through the pipeline run and sample that produced it

Reports must be available in JSON (machine-readable), Markdown (human-readable, LLM-friendly), PDF (formal documentation), and CSV (tabular summaries for spreadsheet review).

---

## Decision

### Data Model Updates

Several fields referenced in the provenance specification do not exist in the current schema. Add them as nullable columns to support forward-compatible provenance reporting. Existing records will have `NULL` for these fields.

**Experiment model -- new columns:**

| Column | Type | Purpose |
| --- | --- | --- |
| `design_type` | String(100) | Experimental design (e.g., cohort, case-control, time-series) |
| `protocol_version` | String(50) | Version of the protocol document |
| `variables_json` | JSONB | Conditions and variables tracked |

**Sample model -- new columns:**

| Column | Type | Purpose |
| --- | --- | --- |
| `parent_sample_id` | Integer (FK, nullable) | Derived sample relationship |
| `collection_timestamp` | DateTime (nullable) | When the sample was collected |
| `collection_method` | String(200, nullable) | Collection protocol |

**File model -- new columns:**

| Column | Type | Purpose |
| --- | --- | --- |
| `sha256_checksum` | String(64, nullable) | SHA-256 for stronger integrity verification |
| `artifact_type` | String(50, nullable) | Classification: raw, cleaned, normalized, filtered, feature_matrix, report, plot |

**Pipeline Run model -- new columns:**

| Column | Type | Purpose |
| --- | --- | --- |
| `retry_count` | Integer (default 0) | Number of retries |
| `reviewed_by_user_id` | Integer (FK, nullable) | Who reviewed the run |
| `reviewed_at` | DateTime (nullable) | When the run was reviewed |

### Report Structure

Each provenance report contains the following sections, populated to the depth appropriate for the entity level:

**Project-level report:**

- Project metadata (ID, name, description, owner, timestamps, status)
- Global configuration (default parameters, reference datasets, environment defaults)
- Audit summary (users who accessed/modified, activity log)
- Experiment index (IDs, names, statuses, sample counts)
- Full lineage DAG (same data as the provenance visualization, serialized)

**Experiment-level report:**

- Experiment metadata (ID, project, name, description, design, protocol, timestamps)
- Sample index with metadata summary
- Pipeline run summaries (IDs, tools, versions, statuses, timestamps)
- Input/output file manifest with checksums
- Audit trail for the experiment

**Sample-level report:**

- Sample metadata (ID, experiment, biological attributes, technical attributes)
- Collection info (timestamp, method)
- Parent/derived relationships
- Linked files (raw and processed) with checksums
- QC metrics and status
- Pipeline runs that processed this sample
- Audit trail for the sample

**Pipeline run report:**

- Run metadata (ID, pipeline name/version, status, timestamps, cost)
- Input lineage (files, samples, references)
- Full parameter set (parameters_json with defaults vs. overrides annotated)
- Execution environment (container versions, k8s pod, CPU/memory)
- Process-level detail (from pipeline_processes: each step's tool, duration, resource usage, exit code, stdout/stderr paths)
- Output files with checksums
- Error messages and logs (if any)
- Retry history

**Artifact (file) report:**

- File metadata (ID, filename, type, size, checksums, storage URI)
- Source lineage (source_type, source_pipeline_run_id -> full run detail)
- Parent sample (if linked via sample_files)
- Downstream usage (pipeline runs that consumed this file as input)
- Audit trail for the file

### Output Formats

**JSON:** Complete structured data. The canonical format -- all other formats are derived from the JSON representation. Schema is versioned (starting at `1.0`) so downstream tools can handle format evolution.

**Markdown:** Human-readable rendering of the JSON data. Uses tables, headers, and code blocks. Designed to be readable in a text editor, GitHub, or by an LLM.

**PDF:** Formal document generated from the Markdown via `weasyprint`. Includes a cover page with entity name, report generation timestamp, and bioAF version. Styled with a clean, professional layout.

**CSV:** Tabular summaries where applicable. One CSV per logical table:

- `sample_manifest.csv` -- one row per sample with all metadata columns
- `file_manifest.csv` -- one row per file with checksums, sizes, URIs
- `pipeline_runs.csv` -- one row per run with key metrics
- `process_steps.csv` -- one row per pipeline process step

### Report Generation Service

A new `ProvenanceReportService` that:

1. Gathers all data for the requested entity (reusing existing queries from `ProvenanceService`, `FileService`, `PipelineRunService`, etc.)
2. Assembles the data into the canonical JSON structure
3. Renders the JSON into Markdown, PDF, and CSV formats
4. Returns either individual format files or a ZIP containing all formats

The service is stateless -- reports are generated on demand, not cached. Report generation is fast because all data lives in the database (no GCS reads required for metadata).

### API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/projects/{id}/provenance/report` | GET | Project provenance report |
| `/api/experiments/{id}/provenance/report` | GET | Experiment provenance report |
| `/api/samples/{id}/provenance/report` | GET | Sample provenance report |
| `/api/pipeline-runs/{id}/provenance/report` | GET | Pipeline run provenance report |
| `/api/files/{id}/provenance/report` | GET | Artifact provenance report |

All endpoints accept a `format` query parameter: `json`, `md`, `pdf`, `csv`, or `all` (ZIP containing all formats). Default is `json`.

### Frontend Integration

Each entity detail page gets a "Provenance Report" section or button:

- **Project detail page:** Button in the provenance tab alongside the existing DAG visualization
- **Experiment detail page:** Button in the header area or a dedicated provenance tab
- **Sample detail page (within experiment):** Action menu item
- **Pipeline run detail page:** Button in the header
- **File detail modal:** "View Provenance" link that opens the artifact report

The UI shows a preview of the report (rendered Markdown) with download options for each format.

### Permissions

Provenance report generation requires the `files:download` permission (same as data export, ADR-036). This keeps the permission model simple -- if you can download data, you can download provenance reports about that data.

### Audit Logging

Provenance report generation is logged:

- Entity type and ID
- Format requested
- User who generated the report
- Timestamp

---

## Consequences

**Positive:**

- Users can produce complete, portable provenance documentation for any entity in the system
- JSON + MD + PDF + CSV covers every consumption pattern: programmatic, human review, formal documentation, spreadsheet analysis
- The data model updates (new nullable columns) are backward-compatible with existing data
- Report generation reuses existing service layer queries rather than duplicating data access logic
- Provenance reports embedded in data exports (ADR-036) ensure reproducibility travels with the data
- Competitive parity with Seven Bridges on provenance reporting

**Negative:**

- `weasyprint` adds system-level dependencies (Cairo, Pango, GDK-PixBuf) to the backend Docker image, increasing image size
- The new nullable columns add minor schema complexity. Existing rows will show NULL for these fields until users populate them.
- PDF generation for large projects (many experiments, many samples) could be slow. Mitigated by generating on demand and keeping reports stateless.

**Neutral:**

- The existing `ProvenanceService` (DAG builder) is not replaced. It continues to serve the visualization. The new `ProvenanceReportService` queries the same data but produces documents instead of graph structures.
- Report schema versioning (`1.0`) allows the format to evolve without breaking consumers.

---

## References

- ADR-036 (Data export and download -- provenance reports included in exports)
- ADR-012 (Customer owns all data -- provenance reports make data self-documenting)
- ADR-014 (GEO export service -- similar ZIP packaging pattern)
- ADR-009 (Immutable audit log -- report generation is audit-logged, audit data feeds into reports)
- ADR-035 (bioaf CLI -- voluntary provenance capture from ad-hoc sessions, complementary to automated provenance)
- ADR-015 (Analysis snapshot SDK -- snapshot data included in provenance reports)
