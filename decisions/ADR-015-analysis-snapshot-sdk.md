# ADR-015: Analysis Snapshot SDK for Iterative Analysis Provenance

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

bioAF provides excellent provenance for structured, automated workflows: pipeline runs record their parameters, inputs, outputs, and container versions (F-032). But the most scientifically important work — interactive analysis in Jupyter and RStudio — is effectively a black box. bioAF tracks *that* a notebook session happened and which files it touched, but not *what* the scientist did inside it.

Computational biologists work iteratively. A typical analysis session involves dozens of parameter variations: trying different clustering resolutions, comparing batch correction methods, adjusting filtering thresholds, testing cell type annotation strategies. The scientist converges on a final result through a process of exploration that is invisible to the platform — and often invisible to the scientist themselves a week later.

This matters for three reasons:

1. **Reproducibility.** When Sarah prepares a publication, she needs to document not just the final parameters but *why* she chose them. "We selected resolution 0.5 because higher resolutions over-split the T cell compartment" requires having the comparison available.
2. **Collaboration.** When Jake takes over an analysis Sarah started, he needs to understand her decision history, not just the final notebook state.
3. **Provenance completeness.** bioAF's provenance chain (ADR-006, F-072) currently has a gap between "pipeline produced h5ad" and "figure appeared in publication." The interactive analysis in between is untracked.

Existing tools (MLflow, Weights & Biases, Neptune) solve this for machine learning workflows but are poorly suited for computational biology. They assume a train/evaluate/deploy paradigm, not an explore/annotate/visualize paradigm. They don't understand AnnData or Seurat objects. They add significant configuration overhead that computational biologists — who are not ML engineers — won't tolerate.

The key design constraint: **the solution must not change how scientists work.** If it requires launching analysis through a bioAF UI, structuring code in a specific way, or adding more than one line of code per checkpoint, adoption will be zero. Computational biologists will simply not use it.

## Decision

bioAF ships a lightweight client library (`bioaf-sdk`) for Python and R that provides a single-function interface for capturing analysis state snapshots. Snapshots are opt-in, explicit, and require exactly one line of code. The SDK reads provenance metadata that scanpy and Seurat already record internally — it does not require the scientist to manually describe what they did.

### Core Interface

**Python (scanpy / AnnData):**

```python
import bioaf

# One-time setup (auto-configured in bioAF-launched notebooks)
bioaf.connect(api_url="https://bioaf.example.com", token="...")

# Capture a snapshot — one line
bioaf.snapshot(adata, label="leiden_0.5_no_batch_correction")
```

**R (Seurat):**

```r
library(bioaf)

# One-time setup (auto-configured in bioAF-launched RStudio)
bioaf_connect(api_url="https://bioaf.example.com", token="...")

# Capture a snapshot — one line
bioaf_snapshot(seurat_obj, label="leiden_0.5_no_batch_correction")
```

### What a Snapshot Captures

The SDK extracts metadata that the analysis tools already record. It does not require the scientist to manually specify parameters.

**From AnnData (scanpy):**

| Field | Source | Example |
|---|---|---|
| Cell count | `adata.n_obs` | 8,432 |
| Gene count | `adata.n_vars` | 18,291 |
| Available embeddings | `list(adata.obsm.keys())` | ["X_pca", "X_umap"] |
| Available clusterings | Categorical columns in `adata.obs` | ["leiden", "leiden_0.3"] |
| Cluster counts per clustering | `adata.obs[col].value_counts()` | {"leiden": {0: 1200, 1: 980, ...}} |
| Method parameters | `adata.uns` | {"neighbors": {"params": {"n_neighbors": 15}}, "leiden": {"params": {"resolution": 0.5}}} |
| Layers present | `list(adata.layers.keys())` | ["counts", "log1p"] |
| Obs columns | `list(adata.obs.columns)` | ["batch", "condition", "leiden", "total_counts"] |
| Var columns | `list(adata.var.columns)` | ["highly_variable", "means", "dispersions"] |
| Raw present | `adata.raw is not None` | True |

**From Seurat:**

| Field | Source | Example |
|---|---|---|
| Cell count | `ncol(seurat_obj)` | 8,432 |
| Gene count | `nrow(seurat_obj)` | 18,291 |
| Available reductions | `Reductions(seurat_obj)` | ["pca", "umap"] |
| Available clusterings | Columns matching cluster patterns in `seurat_obj@meta.data` | ["seurat_clusters", "RNA_snn_res.0.5"] |
| Cluster counts | `table(seurat_obj$seurat_clusters)` | {0: 1200, 1: 980, ...} |
| Command log | `seurat_obj@commands` | Full history of every Seurat function called with all arguments |
| Assays present | `Assays(seurat_obj)` | ["RNA", "SCT"] |
| Default assay | `DefaultAssay(seurat_obj)` | "SCT" |
| Metadata columns | `colnames(seurat_obj@meta.data)` | ["orig.ident", "nCount_RNA", "condition"] |

**Seurat's `@commands` slot is particularly valuable.** Every time a Seurat function is called, it logs the function name, all arguments (including defaults), and a timestamp. This means the Seurat snapshot can reconstruct the *exact sequence of operations* without the scientist doing anything special — it's already recorded by Seurat itself.

### Additional Snapshot Options

```python
# Save a figure with the snapshot
bioaf.snapshot(adata, label="leiden_0.5", figure=plt.gcf())

# Add free-text notes
bioaf.snapshot(adata, label="leiden_0.5", notes="Over-clustered in T cell compartment")

# Tag with experiment (auto-set if launched from experiment page)
bioaf.snapshot(adata, label="leiden_0.5", experiment_id=42)

# Save a lightweight checkpoint (obs + obsm + uns, not the full matrix)
bioaf.snapshot(adata, label="leiden_0.5", save_checkpoint=True)
```

### What a Snapshot Does NOT Capture

- The full expression matrix (too large, and not needed for comparison). Only if `save_checkpoint=True` is the obsm/obs data saved.
- Cell-level data (individual cell barcodes, per-cell embeddings). Only aggregate statistics.
- Code. The snapshot captures *state*, not the code that produced it. The notebook itself (saved as a file) captures the code.

### Snapshot Data Model

```sql
analysis_snapshots (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    experiment_id INTEGER REFERENCES experiments(id),
    compute_session_id INTEGER REFERENCES compute_sessions(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    label VARCHAR(255) NOT NULL,
    notes TEXT,
    
    -- Object metadata
    object_type VARCHAR(20) NOT NULL,  -- 'anndata' or 'seurat'
    cell_count INTEGER,
    gene_count INTEGER,
    
    -- Extracted provenance (structured)
    parameters_json JSONB,         -- method parameters from uns / @commands
    embeddings_json JSONB,         -- available reductions/embeddings
    clusterings_json JSONB,        -- cluster assignments with counts
    layers_json JSONB,             -- available layers/assays
    metadata_columns_json JSONB,   -- obs/meta.data column inventory
    command_log_json JSONB,        -- Seurat @commands (R only; scanpy equiv from uns)
    
    -- Optional attachments
    figure_file_id INTEGER REFERENCES files(id),
    checkpoint_file_id INTEGER REFERENCES files(id),
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
```

Indexed on `(experiment_id, notebook_session_id)` and `(experiment_id, created_at)` for efficient retrieval of snapshot sequences.

### Auto-Configuration in bioAF Notebooks

When a notebook is launched from bioAF's UI (F-040, F-041), the environment is pre-configured:

- `BIOAF_API_URL` and `BIOAF_TOKEN` environment variables are set automatically.
- `BIOAF_EXPERIMENT_ID` is set if launched from an experiment detail page.
- `BIOAF_SESSION_ID` is set to the notebook session's database ID.
- The `bioaf` package is pre-installed in the `bioaf-scrna` conda environment and the R library path.

This means `bioaf.connect()` with no arguments works out of the box in bioAF-launched sessions. For sessions launched outside bioAF (e.g., a scientist's laptop), explicit connection parameters are required.

### SDK Distribution

- **Python:** Published to PyPI as `bioaf-sdk`. Zero heavy dependencies — only `requests` and standard library. AnnData/scanpy are optional imports (the SDK detects what's available).
- **R:** Published to CRAN (or installable from GitHub) as `bioaf`. Dependencies: `httr2`, `jsonlite`. Seurat is a suggested dependency, not required.
- **Both packages are also pre-installed** in bioAF's managed environments.

## Rationale

- **One line of code is the adoption threshold.** Every additional line of configuration, every required structural change to the scientist's workflow, reduces adoption geometrically. `bioaf.snapshot(adata, label="...")` is the absolute minimum surface area.
- **Read what the tools already record.** scanpy writes to `adata.uns`. Seurat writes to `@commands`. The provenance data already exists — the SDK just reads it and ships it. This is fundamentally different from tools like MLflow that require `mlflow.log_param()` calls for every parameter.
- **Aggregate, not cell-level.** Storing per-cell embeddings for every snapshot would be prohibitively expensive (8,000 cells × 50 dimensions × dozens of snapshots). Aggregate statistics (cell count, cluster counts, parameter values) are sufficient for comparison and are tiny (a few KB per snapshot).
- **Opt-in, not automatic.** Automatically snapshotting on every scanpy/Seurat function call was considered and rejected. It would generate enormous noise (dozens of intermediate states per analysis session), create performance overhead, and feel intrusive. Scientists should snapshot when they've reached a meaningful checkpoint worth comparing.
- **Separate from the notebook file.** The notebook captures code; the snapshot captures state. These are complementary. A notebook can be re-executed to reproduce results, but a snapshot tells you *what the results were* without re-executing.

## Consequences

- The `bioaf-sdk` Python and R packages must be developed and maintained alongside the platform. They are lightweight but represent a new surface area.
- The `analysis_snapshots` table is added to the PostgreSQL schema.
- Template notebooks (F-042) should be updated to include `bioaf.snapshot()` calls at key checkpoints, demonstrating the pattern to new users.
- The bioAF API gains new endpoints: `POST /api/v1/snapshots` (create), `GET /api/v1/snapshots?experiment_id=...` (list), `GET /api/v1/snapshots/{id}` (detail).
- This ADR is a prerequisite for ADR-016 (Snapshot Comparison UI), which provides the visual interface for comparing snapshots side by side.
- Future extensions could include automatic diff computation between consecutive snapshots (what changed between snapshot N and N+1), integration with the provenance view (snapshots appear in the experiment timeline), and export of snapshot sequences as supplementary material for publications.
