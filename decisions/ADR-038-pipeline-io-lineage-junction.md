# ADR-038: Pipeline Input File Lineage as a Junction Table

**Status:** Accepted
**Date:** 2026-03-25
**Deciders:** Brent (repository owner)

---

## Context

`PipelineRun` stores input file references in a JSONB column (`input_files_json`). This was pragmatic during early development -- the Nextflow parameter blob that drives a pipeline run is naturally JSON, and embedding file references in it was the path of least resistance.

The problem is that JSONB is opaque to the relational model. Three concrete failures result:

1. **Provenance is broken in one direction.** `File.source_pipeline_run_id` correctly answers "what pipeline produced this file?" But the reverse -- "which pipeline runs consumed this file as input?" -- requires a full table scan with JSON containment operators (`@>`). ADR-037 committed to complete artifact provenance, including downstream usage. That commitment cannot be honored with JSONB.

2. **Referential integrity does not exist.** Deleting a file does not cascade to `input_files_json`. References in that column silently become dangling pointers. The system has no way to detect or prevent this.

3. **Query cost scales poorly.** As pipeline runs accumulate, any query that joins on input file identity (e.g., "all runs that used this reference genome") degrades into a full scan with JSONB extraction. At hundreds of runs per organization this is tolerable; at thousands it is not.

The `output_files_json` column has the same structural problem, but is partially mitigated by `File.source_pipeline_run_id` on the output side. The input side has no mitigation.

---

## Decision

Add a `pipeline_run_input_files` junction table as the authoritative record of which files a pipeline run consumed as inputs.

### New table

```text
pipeline_run_input_files
  id                INTEGER  PK
  pipeline_run_id   INTEGER  FK → pipeline_runs.id  NOT NULL
  file_id           INTEGER  FK → files.id           NOT NULL
  role              VARCHAR(30)  NOT NULL  DEFAULT 'primary_input'
  created_at        TIMESTAMPTZ  DEFAULT now()

  UNIQUE (pipeline_run_id, file_id)
```

`role` values: `primary_input` (FASTQ, BAM passed directly), `reference` (genome, annotation), `supplementary` (samplesheet, config).

### Model changes

`PipelineRun` gains a `input_files` ORM relationship via the junction table.

`File` gains a `consumed_by_runs` back-reference via the same junction.

### What happens to `input_files_json`

Retained as-is. It stores the full Nextflow parameter blob, which contains more than file identifiers -- it includes parameter names, overrides, and non-file inputs. The junction table is not a replacement for that context; it is the queryable, integrity-enforced subset of it.

When a pipeline run is submitted, the service layer writes both: `input_files_json` (full blob for Nextflow) and a row per input file in `pipeline_run_input_files` (for querying and provenance).

A best-effort migration backfills the junction table from existing `input_files_json` rows where the JSON contains parseable file IDs. Rows where the JSON format does not yield file IDs are skipped and left for manual recovery.

### Provenance impact

`ProvenanceDataGatherer.gather_artifact()` currently leaves `downstream_usage` incomplete because it cannot query `input_files_json` efficiently. After this change, it queries `pipeline_run_input_files WHERE file_id = :fid` to populate downstream usage correctly.

### `output_files_json`

Not addressed in this ADR. Output files are already covered by `File.source_pipeline_run_id`. A symmetric `pipeline_run_output_files` junction is deferred until a concrete query need emerges.

---

## Alternatives Considered

**Materialized view over `input_files_json`:** Would enable JOINs without a schema change, but JSONB extraction is fragile (depends on consistent JSON structure across all pipelines) and does not provide referential integrity. Rejected.

**Replace `input_files_json` entirely:** The full Nextflow parameter blob has value beyond file tracking. Replacing it would lose parameter context needed for reproducibility. Rejected.

**Keep status quo:** Provenance completeness (ADR-037) and artifact downstream tracking are broken for all existing and future pipeline runs. Not acceptable given provenance is a core product differentiator. Rejected.

---

## Consequences

**Positive:**

- Artifact provenance downstream usage is now fully queryable and correct.
- File deletion properly signals broken lineage (FK constraint prevents silent dangling references).
- "Which runs consumed this reference genome?" and similar queries become simple JOINs.
- The provenance report (ADR-037) correctly populates `downstream_usage` for all artifacts.

**Negative:**

- Pipeline submission services must write to two places (junction table + JSON blob). A bug that writes one but not the other creates inconsistency. Mitigate with a test asserting both are written on submission.
- Backfill of existing runs is best-effort. Runs where `input_files_json` does not yield file IDs will have incomplete junction records.

**Neutral:**

- `input_files_json` remains and continues to serve its original purpose. The junction table is additive, not a replacement.

---

## References

- ADR-037 (Provenance reporting -- downstream usage of artifacts requires queryable input lineage)
- ADR-009 (Immutable audit log -- provenance chain must be complete)
- ADR-025 (Automated pipeline triggering -- pipeline submission service writes input file records)
