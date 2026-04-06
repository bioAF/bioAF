# ADR-013: MINSEQE-Compliant Structured Metadata Schema

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

bioAF's experiment tracking schema (ADR-006) captures extensive sample and experiment metadata, and supports flexible custom fields via JSON schemas on experiment templates. However, several metadata elements required by the MINSEQE (Minimum Information about a high-throughput Nucleotide SEQuencing Experiment) standard — and by extension MIAME and GEO submission requirements — are currently either absent from the schema or only capturable as free-text custom fields.

The five MINSEQE elements are:

1. **Biological system and sample description** — organism, tissue, experimental factors and their values
2. **Raw sequence read data** — FASTQ files with quality score encoding description
3. **Final processed data** — the data on which conclusions are based, with format descriptions
4. **Experiment overview and sample-data relationships** — summary, contacts, publication info, sample-to-file mapping
5. **Protocols** — sample isolation, library preparation, sequencing instrument, alignment algorithms, data processing methods

bioAF's current schema covers elements 1, 3, and 4 well through the `samples`, `experiments`, `files`, `pipeline_runs`, and `sample_files` tables. Elements 2 and 5 have gaps: sequencing instrument model, library preparation strategy, library layout, molecule type, quality score encoding, and reference genome are not first-class structured fields. They exist only as values buried in `pipeline_runs.parameters_json` or as free-text entries in `experiment_custom_fields`.

This matters because:

- Labs preparing to publish must submit to repositories like GEO, which enforce MIAME/MINSEQE compliance through structured submission templates with specific required fields and controlled vocabularies.
- If these fields are unstructured, the "export to GEO" process becomes a painful manual data-gathering exercise — exactly the kind of workflow bioAF exists to eliminate.
- Structured fields enable meaningful search and filtering in the dataset browser (e.g., "show me all NovaSeq 6000 runs" or "all 10x Chromium 3' v3 libraries").

## Decision

Promote the following metadata elements from free-text/custom fields to first-class structured columns with controlled vocabularies where applicable. These additions complete MINSEQE compliance at the schema level.

### New Columns on `samples`

| Column | Type | Required | Notes |
|---|---|---|---|
| `molecule_type` | VARCHAR(50) | Yes (with default) | Controlled vocabulary matching GEO's seven allowed values: total RNA, polyA RNA, cytoplasmic RNA, nuclear RNA, genomic DNA, protein, other. Default: "total RNA" for scRNA-seq templates. |
| `library_prep_method` | VARCHAR(255) | No | e.g., "10x Chromium 3' v3.1", "Smart-seq2", "Drop-seq". Free text but with autocomplete populated from previously entered values within the org. |
| `library_layout` | VARCHAR(20) | No | Controlled vocabulary: "single-end", "paired-end". |

### New Columns on `batches`

| Column | Type | Required | Notes |
|---|---|---|---|
| `instrument_model` | VARCHAR(255) | No | e.g., "Illumina NovaSeq 6000", "Illumina NextSeq 2000". Autocomplete from previous entries. Batch-level because all samples in a batch typically run on the same instrument. |
| `instrument_platform` | VARCHAR(100) | No | e.g., "Illumina", "PacBio", "Oxford Nanopore". Derived from or validated against `instrument_model`. |
| `quality_score_encoding` | VARCHAR(50) | No | Controlled vocabulary: "Phred+33" (Illumina 1.8+), "Phred+64" (legacy). Default: "Phred+33" for modern Illumina instruments. |

### New Columns on `pipeline_runs`

| Column | Type | Required | Notes |
|---|---|---|---|
| `reference_genome` | VARCHAR(100) | No | e.g., "GRCh38", "GRCm39", "mm10". Extracted from pipeline parameters at run submission time (parseable from `nextflow_schema.json` parameter values). Also stored as a structured field for queryability. |
| `alignment_algorithm` | VARCHAR(255) | No | e.g., "STARsolo", "CellRanger", "Kallisto-Bustools". Derivable from the pipeline name but stored explicitly for search and GEO export. |

### Controlled Vocabulary Strategy

Rather than hard-coding allowed values in the database schema (which would require migrations to update), controlled vocabularies are stored as a new `controlled_vocabularies` table:

```sql
controlled_vocabularies (
    id SERIAL PRIMARY KEY,
    field_name VARCHAR(100) NOT NULL,  -- e.g., 'molecule_type', 'library_layout'
    allowed_value VARCHAR(255) NOT NULL,
    display_order INTEGER DEFAULT 0,
    is_default BOOLEAN DEFAULT FALSE,
    UNIQUE(field_name, allowed_value)
)
```

This table is seeded with GEO's current allowed values during initial deployment and can be updated by admins without schema migrations. The API validates incoming values against this table for fields marked as controlled.

### Migration Approach

- New columns are added as nullable (except `molecule_type` which gets a default value) so existing data is not broken.
- A backfill script attempts to populate `reference_genome` and `alignment_algorithm` from existing `pipeline_runs.parameters_json` and `pipeline_name` values.
- A backfill script attempts to populate `instrument_model` from `batches.sequencer_run_id` where Illumina naming conventions encode the instrument.
- The experiment registration and pipeline launcher UIs are updated to surface these fields.

### Experiment Templates Updated

The default scRNA-seq experiment template is updated to include the new fields with sensible defaults:

- `molecule_type`: "total RNA"
- `library_layout`: "paired-end"
- `quality_score_encoding`: "Phred+33"
- `library_prep_method`: (required, no default — must be specified per experiment)

Admins can modify these defaults per template as with existing custom fields.

## Rationale

- **First-class columns over custom fields.** Custom fields are flexible but unsearchable via SQL without JSON path queries, not validatable against controlled vocabularies, and invisible to the GEO export service. Structured columns enable indexing, validation, and direct mapping to GEO's template.
- **Batch-level instrument fields.** Sequencing instrument and quality encoding are properties of a sequencing run, not individual samples. Since bioAF's `batches` table already represents a sequencing run (with `sequencer_run_id`), these fields belong there. **Addendum (2026-04-05):** With the batch separation introduced in `spec-manifest-driven-ingest`, sample preparation batches (`sample_batches`) and sequencing delivery batches (`sequencing_batches`) are now distinct tables. The instrument columns remain on `sample_batches` for labs that track prep equipment (microtome blades, thermocyclers, autoclaves, etc.) -- this is valid MINSEQE element 5 documentation of sample processing protocols. The canonical source for GEO export's instrument_model (the sequencing instrument used by the CRO/core) is now `sequencing_batches`. Both tables carry these columns independently, serving different purposes.
- **Pipeline-level reference genome and alignment.** These are chosen at processing time, not sample collection time. Storing them on `pipeline_runs` preserves the truth that the same samples can be processed against different references.
- **Controlled vocabulary table over ENUMs.** PostgreSQL ENUMs require migrations to add values. A lookup table lets admins adapt to new GEO requirements (e.g., if GEO adds a new molecule type) without developer intervention.
- **Autocomplete over strict validation for some fields.** Library prep methods and instrument models evolve faster than any controlled vocabulary can track. Autocomplete from previously entered values provides consistency without blocking novel entries.

## Consequences

- A database migration (Phase 7 or a point release within Phase 6) adds the new columns and the `controlled_vocabularies` table.
- The experiment registration form gains new fields. Care must be taken to avoid overwhelming bench scientists — these fields should be in an "advanced" or "sequencing details" section, not the primary form.
- The pipeline launcher should auto-populate `reference_genome` and `alignment_algorithm` from the selected pipeline's parameters where possible, reducing manual entry.
- The dataset browser gains new filter dimensions (instrument, molecule type, library prep, reference genome).
- Existing experiments and pipeline runs will have NULL values for the new columns until backfilled. The GEO export service must handle NULLs gracefully by flagging incomplete fields rather than failing.
- This ADR is a prerequisite for ADR-014 (GEO Export Service).
