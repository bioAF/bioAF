# ADR-014: GEO Export Service for Publication-Ready Data Submission

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

Publishing results from sequencing experiments requires submitting raw and processed data to public repositories. For the vast majority of bioAF's target users — small biotech teams doing single-cell and bulk RNA-seq — the relevant repository is NCBI's Gene Expression Omnibus (GEO). GEO enforces MIAME and MINSEQE compliance through a structured Excel submission template with specific required fields, controlled drop-down values, and a defined file upload process.

Today, preparing a GEO submission is a manual, error-prone process:

1. Download GEO's Excel template.
2. Manually transcribe experiment metadata, sample annotations, and protocol descriptions from internal tracking systems (or spreadsheets, or memory) into the template.
3. Compute MD5 checksums for all raw and processed data files.
4. Upload files via FTP.
5. Submit the metadata spreadsheet through GEO's web interface.
6. Wait for curator review, fix errors, resubmit.

Steps 1–3 are where most of the pain lives, and they map directly to data bioAF already captures in its database (experiments, samples, batches, pipeline runs, files). With the structured metadata additions from ADR-013, bioAF has all the information needed to auto-generate a valid GEO submission package.

This is a high-value feature for the publication workflow. Sarah (computational biologist persona) and Maria (CSO persona) both care deeply about reducing time-to-publication. Jake (bioinformatician persona) is typically the person stuck doing this work manually.

## Decision

bioAF will include a GEO Export Service that generates a submission-ready package from an experiment's structured metadata. The export produces GEO's Excel template pre-filled with all available data, a file manifest with MD5 checksums, and a validation report flagging any missing required fields.

### Export Output

The GEO export produces a ZIP archive containing:

| File | Contents |
|---|---|
| `geo_metadata_{experiment_name}.xlsx` | GEO's high-throughput sequencing metadata template, pre-filled |
| `md5_checksums.txt` | MD5 checksums for all raw and processed data files, in GEO's expected format |
| `validation_report.json` | Machine-readable report of completeness: which fields are populated, which are missing, which have values outside GEO's controlled vocabularies |
| `validation_report.txt` | Human-readable version of the above, suitable for printing or sharing |
| `README.txt` | Instructions for completing the submission (FTP upload steps, how to notify GEO) |

The export does **not** include the actual data files (FASTQs, processed matrices). These are typically too large to bundle into a ZIP and must be uploaded to GEO's FTP server separately. The README includes the GCS paths for all files so the user can transfer them.

### Metadata Mapping

The service maps bioAF's internal schema to GEO's template columns:

**SERIES section (experiment-level):**

| GEO Field | bioAF Source |
|---|---|
| Series title | `experiments.name` |
| Series summary | `experiments.description` + `experiments.hypothesis` |
| Series overall design | Generated from sample count, organism, tissue types, and experimental factors |
| Contributor(s) | `experiments.owner_user_id` → `users.name` + org member names |
| Supplementary file | GCS URIs of processed data files linked to the experiment |

**SAMPLES section (sample-level):**

| GEO Field | bioAF Source |
|---|---|
| Sample name | `samples.sample_id_unique` |
| title | `samples.sample_id_unique` + `samples.tissue_type` + `samples.treatment_condition` |
| organism | `samples.organism` |
| molecule | `samples.molecule_type` (ADR-013) |
| tissue | `samples.tissue_type` |
| cell type | From custom fields or tissue type |
| cell line | From custom fields (if applicable) |
| treatment | `samples.treatment_condition` |
| genotype | From custom fields (if applicable) |
| description | `samples.prep_notes` + QC notes |
| instrument model | `batches.instrument_model` (ADR-013) |
| library strategy | Derived from `samples.library_prep_method` (ADR-013). Mapped to GEO's controlled vocabulary (e.g., "RNA-Seq", "OTHER"). |
| library source | Derived from `samples.molecule_type` (e.g., "total RNA" → "TRANSCRIPTOMIC") |
| library selection | Derived from `samples.library_prep_method` (e.g., "10x Chromium" → "cDNA") |
| library layout | `samples.library_layout` (ADR-013) |
| data processing | Assembled from `pipeline_runs.pipeline_name`, `pipeline_runs.pipeline_version`, `pipeline_runs.alignment_algorithm`, `pipeline_runs.reference_genome`, and `pipeline_runs.parameters_json` |
| processed data file | Filenames from `files` linked to the experiment's pipeline run outputs |
| raw file | FASTQ filenames from `files` linked to samples via `sample_files` |

**PROTOCOLS section:**

| GEO Field | bioAF Source |
|---|---|
| Growth protocol | From experiment custom fields or linked protocol documents |
| Treatment protocol | From experiment custom fields or linked protocol documents |
| Extract protocol | From experiment custom fields or linked protocol documents |
| Library construction protocol | `samples.library_prep_method` + `samples.chemistry_version` + linked protocol documents |
| Data processing steps | Ordered list assembled from pipeline run parameters, software versions, and reference genome |
| Genome build | `pipeline_runs.reference_genome` (ADR-013) |

### Validation Rules

Before generating the export, the service runs a validation pass and categorizes each field as:

- **Complete** — field is populated and matches GEO's controlled vocabulary where applicable.
- **Populated but unvalidated** — field has a value but it's not in GEO's known vocabulary (e.g., a novel instrument model). GEO may accept it, but the user should verify.
- **Missing (required)** — GEO requires this field and it's empty in bioAF. The export will include a placeholder value (e.g., `[REQUIRED - please fill in]`) and the validation report will flag it.
- **Missing (recommended)** — GEO doesn't strictly require this but submissions are often rejected without it. Flagged as a warning.

### API and UI

**API endpoint:**

```text
POST /api/v1/experiments/{experiment_id}/export/geo
```

Returns a ZIP file as a download. Accepts optional query parameters for which pipeline run to use (if multiple exist for the experiment) and whether to include only samples with a specific QC status.

**UI integration:**

- "Export to GEO" button on the experiment detail page, visible to comp_bio and admin roles.
- Clicking it shows a pre-export validation summary: green checkmarks for complete fields, yellow warnings for recommended-but-missing fields, red flags for required-but-missing fields.
- The user can proceed with export even if there are warnings/errors (the template will contain placeholders they can fill in manually).
- After export, the validation summary is logged in the audit trail.

### GEO Template Versioning

GEO occasionally updates their Excel template (new fields, changed column names, updated controlled vocabularies). The export service uses a template definition file (`geo_template_definition.json`) that maps bioAF fields to GEO columns, specifies controlled vocabularies, and defines the Excel layout. This definition file is:

- Shipped with each bioAF release (updated when GEO changes their template).
- Overridable by admins who need to accommodate GEO changes between bioAF releases.
- Version-tracked so the export includes which template version was used.

## Rationale

- **GEO is the universal target.** While other repositories exist (ArrayExpress, SRA, DDBJ), GEO is the most widely used for gene expression studies and its submission format is the de facto standard. GEO brokers raw data to SRA automatically, so a GEO submission satisfies both repositories.
- **Export, not direct submission.** bioAF generates the submission package but does not submit directly to GEO's API (GEO doesn't have a robust programmatic submission API — it's FTP + web form). This keeps the feature simple and avoids brittleness from depending on GEO's infrastructure.
- **Validation before export reduces rejection cycles.** GEO curators manually review submissions and reject those with missing or incorrect fields. Pre-export validation catches most issues before the user uploads to GEO, reducing round-trips that can add weeks to the publication timeline.
- **Covering GEO covers all three standards.** GEO's submission template is designed to satisfy both MIAME and MINSEQE. If a bioAF export produces a valid GEO submission, the underlying data is MIAME/MINSEQE compliant by construction. This means bioAF doesn't need separate MIAME or MINSEQE export features.
- **The template definition approach handles GEO's evolution.** GEO has changed their template multiple times over the years. A data-driven mapping (JSON definition file) is more maintainable than hard-coded Excel generation logic.

## Consequences

- ADR-013 (MINSEQE-compliant metadata schema) is a hard prerequisite. The GEO export cannot produce a valid submission without the structured fields it introduces.
- The `openpyxl` Python library (or equivalent) is added as a backend dependency for Excel generation.
- The GEO template definition file must be maintained as GEO updates their requirements. This is a low-frequency maintenance task (GEO changes their template roughly once per year).
- The export feature becomes a strong incentive for bench scientists to fill in metadata completely at experiment registration time — they'll see the validation warnings at export time if they didn't.
- Future extensions could include export to other repositories (ArrayExpress, SRA direct, CellxGene Census) using the same architecture: a repository-specific template definition file + a mapping from bioAF's schema.
- The MD5 checksum generation for large FASTQ files may be slow if checksums weren't computed at upload time. The upload service (Phase 5) already computes MD5s, so this should be a lookup, not a computation. If historical files are missing checksums, the export service flags them in the validation report rather than computing them on the fly.
