# ADR-040: Notebook Session File Lifecycle

**Status:** Accepted
**Date:** 2026-04-04
**Deciders:** Brent (repository owner)

---

## Context

Notebook and SSH sessions can launch with input files and produce output files, but the current implementation has three gaps that prevent scientists from working effectively with their data:

**Gap 1: Flat input file structure.** Input files are copied to `/data/` with no directory organization. A pipeline run producing `star/001/001.Log.final.out` and `star/001/001.SJ.out.tab` alongside `multiqc/multiqc_report.html` dumps all files into a single flat directory. Scientists cannot navigate files by their logical grouping, and files with the same basename from different subdirectories overwrite each other.

**Gap 2: No designated output directory.** Scientists generate analysis outputs (figures, filtered matrices, exported CSVs) during sessions, but there is no convention for where these files should go. The home directory is synced to GCS on shutdown, but this captures everything (shell history, caches, config files) alongside actual outputs. There is no separation of signal from noise.

**Gap 3: Output files are not persisted to the results bucket.** The home directory sync writes to the working bucket, which is for in-progress data. Finalized session outputs should move to the results bucket so they are discoverable alongside pipeline outputs and uploads. Currently, a figure produced in a notebook session is effectively lost once the session terminates unless the scientist manually uploads it.

ADR-039 established the `NotebookSessionFile` model and `notebook_output` source type for provenance tracking. This ADR defines the file lifecycle that produces and consumes those records.

---

## Decision

### 1. Hierarchical input file organization

Input files mounted at session start are organized under `/data/` using the entity hierarchy:

```text
/data/{project_name}/{experiment_name}/{sample_name}/{tool_name}/filename
/data/{project_name}/uploads/filename
```

The path is derived from each file's associations in the database (project, experiment, sample, and the pipeline or tool that produced it). Uploaded files that are not associated with a specific experiment or sample go into an `uploads/` directory within their project scope.

This structure lets scientists navigate files by the logical units they already think in: "I want the STARsolo output for sample 16 in experiment 2."

The init container commands include `mkdir -p` to create intermediate directories before copying files.

### 2. Designated `/outputs/` directory

All session types (Jupyter, RStudio, SSH) get a writable `/outputs/` directory backed by an `emptyDir` volume. The contract is simple: anything saved to `/outputs/` is persisted to GCS on shutdown and registered as a tracked output file. Everything outside `/outputs/` (except notebook scripts, see below) is ephemeral.

SSH work nodes retain their existing `/scratch/` volume for temporary computation. The `/outputs/` directory is additive.

### 3. Notebook and script capture

On shutdown, the platform captures analysis scripts from the home directory: `.ipynb`, `.Rmd`, `.R`, and `.py` files (up to 3 levels deep). These are the analysis recipes that transform inputs into outputs and are critical for reproducibility. They are persisted alongside other output files.

### 4. Two-phase output persistence

Output files follow a two-phase GCS lifecycle:

- **During the session:** `/outputs/` contents are synced to the working bucket at `gs://{working_bucket}/sessions/{session_id}/outputs/`. This happens periodically or on explicit save. The working bucket is for in-progress data.
- **On session close:** Output files are copied from the working bucket to the results bucket at `gs://{results_bucket}/sessions/{session_id}/outputs/`. The results bucket is for finalized, discoverable data. Output files are then registered as `File` records with `source_type=notebook_output` and linked to the session's project, experiment, and sample associations via `NotebookSessionFile`.

This separation means the working bucket can have aggressive lifecycle policies (e.g., delete after 30 days) without affecting finalized results.

### 5. Extended shutdown timeout

Large bioinformatics outputs (h5ad files, RDS objects) can be 10GB+. The shutdown flow is extended to a 30-minute timeout to accommodate GCS sync of large files. The UI displays a status indicator during sync so the scientist knows the session is persisting their work, not hung.

### 6. Schema changes

**`compute_sessions` table -- new column:**

| Column | Type | Purpose |
| --- | --- | --- |
| `gcs_output_prefix` | String(500), nullable | GCS prefix where outputs were persisted on shutdown |

No other schema changes are required. The `NotebookSessionFile` model (ADR-039) and `File.source_type=notebook_output` already exist.

---

## Alternatives Considered

**Mount GCS directly via FUSE for outputs:** GCS FUSE would give scientists a live-mounted output directory without a sync step. Rejected because FUSE write performance is poor for the small-file, random-write patterns typical of interactive analysis (creating many small PNGs, CSVs, and intermediate files). The emptyDir + sync-on-close approach provides local-disk write performance with GCS durability.

**Sync the entire home directory as outputs:** This is the current behavior. Rejected because it captures system files (.bash_history, .cache, .local) alongside actual outputs, making provenance noisy and output discovery unreliable.

**Single bucket for working and results:** Simpler, but conflates in-progress session data with finalized outputs. Scientists searching for results would need to filter out incomplete sessions. Rejected in favor of the two-bucket approach which provides a clean boundary.

---

## Consequences

**Positive:**

- Scientists see their data organized by project, experiment, sample, and tool -- matching their mental model
- The `/outputs/` convention provides a clear, simple contract for what gets persisted
- Two-phase persistence means working data can be aggressively cleaned without affecting results
- Notebook scripts are captured automatically, preserving the analysis recipe for reproducibility
- Output files are registered with full provenance linkage (ADR-039), answering "which session produced this file?"

**Negative:**

- The 30-minute shutdown timeout means session termination is not instant for large outputs. The UI indicator mitigates confusion but does not eliminate the wait.
- Scientists must remember to save outputs to `/outputs/` rather than their home directory. Training and clear UI messaging are needed. Files saved elsewhere are lost on termination.
- The two-phase copy (working then results) doubles GCS write operations for output files. Given the relatively small volume of notebook outputs compared to pipeline outputs, this cost is acceptable.

**Neutral:**

- SSH work nodes gain `/outputs/` alongside their existing `/scratch/`. The two directories serve different purposes (persistent vs. ephemeral) and do not conflict.
- The hierarchical input structure requires resolving file associations at launch time. This adds a few database queries to the launch flow but does not affect session startup latency meaningfully since the queries run while the node is scaling up.

---

## References

- ADR-039 (Notebook output provenance -- `NotebookSessionFile` model and `notebook_output` source type)
- ADR-037 (Provenance reporting -- artifact lineage requires session input/output tracking)
- ADR-033 (Versioned compute environments -- environment version linking for sessions)
- ADR-034 (Custom work nodes -- SSH session architecture, `/scratch/` volume)
- ADR-022 (GCS storage backend -- bucket architecture and lifecycle policies)
