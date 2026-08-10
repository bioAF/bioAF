# ADR-017: Managed Reference Data Layer

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

Every computational biology analysis depends on external reference data: genome sequences, gene annotations, genome indices, reference cell atlases, marker gene lists, and gene ontology databases. In bioAF's current architecture, there is no concept of reference data. The GCS bucket structure (raw, working, results) is designed for experiment-specific data. Reference data: which is shared across all experiments and all users, has no managed home.

In practice, this means one of two things happens at every lab:

1. Someone manually downloads reference files to a shared directory on the cluster. Everyone uses them. Nobody tracks versions. Six months later, someone updates the genome annotation and silently breaks reproducibility for every pipeline run that used the old version.
2. Each user downloads their own copy of reference data into their home directory or working space. Disk usage balloons. Different users end up using different versions of the same reference without realizing it.

Both scenarios are common and both undermine bioAF's core promise of provenance and reproducibility. If a pipeline run used GRCh38 + GENCODE v43, but the reference directory has since been updated to GENCODE v44, re-running the pipeline silently produces different results, and there's no record of the change.

The key insight from the product owner: **"If Brent added a file that in no way impacts my run, I don't care. If he replaced a file I need, I very much care."** This means versioning alone is insufficient. The system must be *impact-aware*: capable of answering "which pipeline runs used this reference version, and are any of them affected by this change?"

## Decision

bioAF introduces a managed reference data layer: a dedicated GCS bucket, a metadata registry in PostgreSQL, and governance rules that distinguish between public curated references and lab-internal references.

### Storage

A new GCS bucket `bioaf-references-{org}` is added to the Terraform storage configuration, with the same protections as other data buckets (versioning enabled, delete protection, cannot be disabled via bioAF).

The bucket is FUSE-mounted on all compute nodes and notebook servers at `/data/references/`, alongside the existing `/data/raw/`, `/data/working/`, and `/data/results/` mounts.

Directory structure:

```text
/data/references/
├── genomes/
│   ├── GRCh38/
│   │   ├── v43/                    ← GENCODE release
│   │   │   ├── genome.fa
│   │   │   ├── genome.fa.fai
│   │   │   ├── genes.gtf
│   │   │   └── star_index/
│   │   └── v44/
│   └── GRCm39/
│       └── M33/
├── atlases/
│   ├── tabula_sapiens_v1/
│   └── human_cell_landscape_v2/
├── markers/
│   └── custom_germ_cell_markers_v3.csv
└── indices/
    ├── cellranger_GRCh38_2024A/
    └── star_GRCh38_gencode_v43/
```

The path structure encodes the reference identity and version, making it human-readable and POSIX-friendly. Scientists reference data at paths like `/data/references/genomes/GRCh38/v43/genes.gtf`: no API calls needed for read access.

### Metadata Registry

```sql
reference_datasets (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    name VARCHAR(255) NOT NULL,           -- e.g., "GRCh38 GENCODE v43"
    category VARCHAR(50) NOT NULL,        -- 'genome', 'annotation', 'index', 'atlas', 'markers', 'other'
    scope VARCHAR(20) NOT NULL,           -- 'public' or 'internal'
    version VARCHAR(100) NOT NULL,
    source_url TEXT,                       -- where it was downloaded from (e.g., GENCODE FTP URL)
    gcs_prefix TEXT NOT NULL,             -- GCS path prefix (e.g., "genomes/GRCh38/v43/")
    total_size_bytes BIGINT,
    file_count INTEGER,
    md5_manifest_json JSONB,              -- {filename: md5_checksum} for every file in the dataset
    uploaded_by_user_id INTEGER REFERENCES users(id),
    approved_by_user_id INTEGER REFERENCES users(id),  -- NULL for internal scope
    status VARCHAR(20) NOT NULL DEFAULT 'active',      -- 'active', 'deprecated', 'pending_approval'
    deprecation_note TEXT,                -- why this version was deprecated
    superseded_by_id INTEGER REFERENCES reference_datasets(id),  -- points to the newer version
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(organization_id, name, version)
)

reference_dataset_files (
    id SERIAL PRIMARY KEY,
    reference_dataset_id INTEGER NOT NULL REFERENCES reference_datasets(id),
    filename VARCHAR(500) NOT NULL,
    gcs_uri TEXT NOT NULL,
    size_bytes BIGINT,
    md5_checksum VARCHAR(32),
    file_type VARCHAR(50),               -- 'fasta', 'gtf', 'index', 'h5ad', 'csv', etc.
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

### Pipeline Run Linkage

When a pipeline run uses a reference dataset, the linkage is recorded:

```sql
pipeline_run_references (
    pipeline_run_id INTEGER NOT NULL REFERENCES pipeline_runs(id),
    reference_dataset_id INTEGER NOT NULL REFERENCES reference_datasets(id),
    PRIMARY KEY (pipeline_run_id, reference_dataset_id)
)
```

This is populated automatically when a pipeline is launched: the pipeline launcher inspects the parameter values for paths under `/data/references/` and resolves them to reference dataset records. It can also be populated manually for notebook-based analyses via the snapshot SDK (ADR-015).

### Impact-Aware Versioning

When a reference dataset is updated (a new version is added, or an existing version is deprecated), the system computes the **impact set**: all pipeline runs and analysis snapshots that used the previous version.

The impact assessment is surfaced in three places:

1. **At update time.** When an admin uploads a new version of a reference or deprecates an existing one, the UI shows: "This reference was used in N pipeline runs across M experiments. The following experiments may be affected: [list]."

2. **On the pipeline run detail page.** If any reference used by a pipeline run has been superseded, a warning badge appears: "Reference data updated since this run. GRCh38 GENCODE v43 → v44 (uploaded Mar 15 by Jake). Results may differ if re-run."

3. **In the provenance view.** Reference datasets appear as nodes in the provenance graph, linked to pipeline runs. Deprecated references are visually flagged.

### Two Governance Models

**Public curated references (`scope = 'public'`):**

These are shared across the organization and used by pipelines. Changes affect everyone.

- Uploading a new version requires admin or comp_bio role.
- Replacing or deprecating an active version requires admin approval (status goes to `pending_approval` until an admin confirms).
- The approval step shows the impact assessment before the admin confirms.
- bioAF ships with a seed script that pre-downloads common references during initial SLURM provisioning: GRCh38 + GENCODE (latest stable), GRCm39 + GENCODE (latest stable), and CellRanger-compatible pre-built indices if CellRanger is available.

**Lab-internal references (`scope = 'internal'`):**

These are custom reference files created by the team: marker gene lists, custom atlases, in-house annotation models.

- Any comp_bio or admin user can upload or update.
- No approval required, but every change is logged in the audit trail and triggers a notification to users who have used the previous version in a pipeline run within the last 90 days.
- Internal references can optionally be linked to an experiment (e.g., "this marker gene list was curated as part of experiment 23").

### Pre-Seeded References

During initial SLURM provisioning (or on first enable), bioAF offers to download a standard set of references. The admin selects which organisms are relevant:

- **Human:** GRCh38 genome + GENCODE annotation (latest stable) + STAR index + CellRanger index (if available)
- **Mouse:** GRCm39 genome + GENCODE annotation (latest stable) + STAR index + CellRanger index (if available)

Downloads are background tasks with progress tracking in the UI. The reference registry is populated automatically as files are downloaded.

### API Endpoints

```text
GET    /api/v1/references                        → List all reference datasets (filterable by category, scope, status)
GET    /api/v1/references/{id}                    → Detail with file manifest and impact summary
POST   /api/v1/references                         → Upload new reference dataset
POST   /api/v1/references/{id}/deprecate           → Deprecate with impact assessment
GET    /api/v1/references/{id}/impact              → Which pipeline runs / experiments used this version
GET    /api/v1/pipeline-runs/{id}/references       → Which references were used by this run
```

## Rationale

- **POSIX paths are non-negotiable.** Scientists reference data by file path in their code: `sc.read("/data/references/atlases/tabula_sapiens_v1/...")`. Any solution that requires API calls to access reference data will not be adopted. The FUSE mount ensures reference data appears as normal files on the filesystem.
- **Impact awareness is the differentiator.** Every lab has a `/data/references` directory. What they don't have is an answer to "what happens if I update this genome annotation?" Impact-aware versioning makes reference updates a deliberate, informed decision rather than a silent time bomb.
- **Two governance models match reality.** Public genome annotations require care: an accidental update can invalidate months of work. Custom marker gene lists are more fluid and don't need the same ceremony. One governance model for both would be either too permissive (dangerous for genomes) or too cumbersome (annoying for marker lists).
- **Pipeline run linkage closes the provenance gap.** Without reference linkage, the provenance chain has a hidden dependency: the pipeline run records its parameters but not which genome version was at the referenced path when it ran. With linkage, reproducibility is fully specified.
- **Pre-seeding reduces time-to-first-pipeline.** Downloading and indexing a genome takes hours. If this happens automatically during setup, the first pipeline run can start immediately.

## Consequences

- A new GCS bucket (`bioaf-references-{org}`) is added to the Terraform storage configuration.
- The FUSE mount configuration on compute nodes is updated to include `/data/references/`.
- The `reference_datasets`, `reference_dataset_files`, and `pipeline_run_references` tables are added to the PostgreSQL schema.
- The pipeline launcher must resolve reference paths to registry records at submission time. This requires path-matching logic that maps a filesystem path (e.g., `/data/references/genomes/GRCh38/v43/`) to a reference dataset record.
- The pre-seeding download during SLURM provisioning adds time to the initial setup. This should be a background task that doesn't block the provisioning completion, with progress visible in the UI.
- Storage cost for reference data is modest (a human genome + index is ~30GB; mouse is similar) but should be surfaced in the storage dashboard (F-013).
- This ADR interacts with ADR-013 (MINSEQE compliance): the `pipeline_runs.reference_genome` field from ADR-013 can be auto-populated from the reference registry rather than requiring manual entry.
