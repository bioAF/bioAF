# ADR-023: Configurable CRO Naming Profiles

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

Biotech companies routinely outsource sequencing and primary data processing to Contract Research Organizations (CROs). CROs return processed data files (FASTQs, BAMs, count matrices) with file names that follow a structured naming convention encoding project, experiment, sample, date, data type, researcher, and version information.

Example: `2026-03-10_ProjectX_RNASeq_DiffExpr_SmithE_v001.txt`

These naming conventions are standardized within a given CRO engagement but differ between CROs. When a company switches CROs or works with multiple simultaneously, the naming scheme changes. bioAF needs to parse these file names to automatically catalog incoming data and link it to the correct project, experiment, and sample records — but the parsing rules must be configurable rather than hardcoded.

### Industry Conventions

Standard elements in bioinformatics file naming:

- Project or experiment name/acronym
- Date in YYYY-MM-DD format
- Researcher or team initials
- Data type (RNA-Seq, ChIP-Seq, differential expression, etc.)
- Version number (e.g., v001, v002)
- File extension (.fastq, .bam, .h5ad, .txt, .pdf)

Best practices include underscore or camelCase separators (no spaces or special characters), leading zeros for sequential numbers, and a readme.txt documenting the convention.

---

## Decision

Implement a configurable naming profile system that allows administrators to define how incoming file names are parsed and mapped to bioAF entities. Profiles are managed through the admin UI and stored in the database. Multiple profiles can exist simultaneously; the auto-ingest system (ADR-024) attempts to match incoming files against active profiles.

### Profile Definition Schema

A naming profile defines:

1. **Name and description:** Human-readable identifier (e.g., "GenomicsCorpCRO Standard", "Internal Lab Convention")
2. **Delimiter:** The character used to separate segments (typically `_`)
3. **Segment mapping:** An ordered list specifying what each position in the filename represents
4. **Status:** Active or inactive. Only active profiles are used for matching.

```json
{
  "name": "GenomicsCorp CRO Standard",
  "description": "Naming convention used by GenomicsCorp for RNA-Seq deliveries",
  "delimiter": "_",
  "strip_extension": true,
  "segments": [
    {
      "position": 0,
      "field": "date",
      "format": "YYYY-MM-DD",
      "required": true
    },
    {
      "position": 1,
      "field": "project_code",
      "maps_to": "project",
      "required": true
    },
    {
      "position": 2,
      "field": "data_type",
      "maps_to": "experiment_metadata.data_type",
      "required": true
    },
    {
      "position": 3,
      "field": "analysis_type",
      "maps_to": "experiment_metadata.analysis_type",
      "required": false
    },
    {
      "position": 4,
      "field": "researcher_initials",
      "maps_to": "experiment_metadata.researcher",
      "required": false
    },
    {
      "position": 5,
      "field": "version",
      "format": "v###",
      "maps_to": "file_metadata.version",
      "required": true
    }
  ],
  "project_code_mappings": {
    "ProjectX": "uuid-of-bioaf-project-x",
    "GBMAtlas": "uuid-of-bioaf-gbm-atlas"
  },
  "status": "active"
}
```

### Segment Field Types

| Field | Description | Maps To |
|---|---|---|
| `date` | File date, parsed according to `format` | `files.file_date` |
| `project_code` | Short code identifying the project | Looked up in `project_code_mappings` -> `projects.id` |
| `experiment_code` | Short code identifying the experiment | Looked up in `experiment_code_mappings` -> `experiments.id` |
| `sample_id` | External sample identifier | `samples.sample_id_unique` |
| `data_type` | Type of data (RNA-Seq, ChIP-Seq, etc.) | Stored as experiment or file metadata |
| `analysis_type` | Type of analysis | Stored as file metadata |
| `researcher_initials` | Researcher who produced the file | Stored as file metadata |
| `version` | File version number | `files.version` |
| `batch_id` | Batch identifier | Looked up against `batches` |
| `organism` | Organism code | `samples.organism` |
| `ignore` | Segment is parsed but not mapped | Discarded |

### Project and Experiment Code Mappings

The profile includes a mapping table that translates CRO-specific codes to bioAF entity IDs. For example, `"ProjectX"` in the filename maps to the actual bioAF project record for "Project X - GBM Single Cell Atlas."

- **Mapped codes:** File is automatically linked to the correct project/experiment
- **Unmapped codes:** The auto-ingest system (ADR-024) auto-creates the project/experiment in "unclaimed" status, as described in that ADR

Administrators configure these mappings through the naming profile UI. Mappings can be added at any time — including retroactively, which triggers a re-evaluation of previously unclaimed files.

### Profile Matching Logic

When a file arrives in the ingest bucket:

1. The filename (minus extension if `strip_extension` is true) is split by each active profile's delimiter
2. For each active profile, the system checks whether the segment count matches and required segments parse successfully
3. If exactly one profile matches, the file is parsed according to that profile
4. If multiple profiles match, the file is flagged for manual review with the candidate profiles listed
5. If no profile matches, the file is cataloged with its raw filename and flagged as "unmatched" for manual review

### Database Schema

```sql
-- Naming profile definitions
naming_profiles (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL REFERENCES organizations(id),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  delimiter VARCHAR(10) NOT NULL DEFAULT '_',
  strip_extension BOOLEAN NOT NULL DEFAULT true,
  segments_json JSONB NOT NULL,
  project_code_mappings JSONB NOT NULL DEFAULT '{}',
  experiment_code_mappings JSONB NOT NULL DEFAULT '{}',
  status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active, inactive
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
)

-- Parse results for each ingested file
file_parse_results (
  id UUID PRIMARY KEY,
  file_id UUID NOT NULL REFERENCES files(id),
  naming_profile_id UUID REFERENCES naming_profiles(id),  -- null if unmatched
  parsed_segments_json JSONB,  -- raw parse output
  match_status VARCHAR(20) NOT NULL,  -- matched, multiple_matches, unmatched
  auto_linked BOOLEAN NOT NULL DEFAULT false,
  reviewed_by UUID REFERENCES users(id),
  reviewed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
)
```

### Admin UI

The naming profile management page (accessible under Settings or Data & Files) provides:

- **Profile list:** All profiles with name, status, match statistics (files matched in last 30 days)
- **Profile editor:** Visual segment mapper with drag-and-drop reordering, format validation, and a test parser where the admin can paste example filenames to verify parsing
- **Code mapping table:** Add/edit/remove project and experiment code mappings with autocomplete against existing bioAF records
- **Test parser:** Paste one or more filenames, see how each active profile would parse them, verify correct entity linking before activating a profile

---

## Consequences

**Positive:**

- Incoming data is automatically parsed and cataloged without manual intervention
- CRO naming conventions are captured as configuration, not code — no engineering effort to support a new CRO
- Multiple CROs can be supported simultaneously with different active profiles
- The test parser prevents misconfiguration before it affects real data
- Unmapped codes auto-create entities rather than silently failing, ensuring no data is lost

**Negative:**

- Naming profiles add configuration complexity for administrators
- Ambiguous filenames (matching multiple profiles) require manual resolution
- Profiles are only as good as the CRO's adherence to their own convention — inconsistent naming from the CRO will produce parse failures
- The assumption that filenames are the primary identifier may not hold for all data delivery methods (e.g., CROs that deliver via API or structured manifests)

**Neutral:**

- The readme.txt that CROs sometimes include with deliveries is not parsed automatically, but can be uploaded as a document and linked to the batch

---

## References

- ADR-024 (GCS event-driven auto-ingest — consumes parse results)
- ADR-013 (MINSEQE metadata — parsed fields contribute to metadata completeness)
