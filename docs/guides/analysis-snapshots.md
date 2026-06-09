# Analysis Snapshots

bioAF's Analysis Snapshot SDK captures structured metadata from AnnData (Python) and Seurat (R) objects at scientist-defined checkpoints during interactive analysis. Snapshots enable provenance tracking, comparison of parameter variations, and reproducibility for publication.

## Concepts

An analysis snapshot records the state of your single-cell analysis at a specific moment:

- Cell and gene counts (post-filtering).
- Clustering parameters and resulting cluster assignments.
- Dimensionality reduction coordinates (UMAP, t-SNE, PCA).
- Marker genes per cluster.
- Custom metrics you define.
- A human-readable description of what you were testing.

Snapshots are lightweight metadata records stored in PostgreSQL, not copies of the full data object. A typical snapshot is 10-100KB regardless of dataset size.

## Python SDK (AnnData)

### Installation

The bioAF Python SDK is pre-installed in bioAF-managed Jupyter environments. For external environments:

```bash
pip install bioaf-sdk
```

### Configuration

At the start of your notebook, initialize the SDK:

```python
from bioaf import SnapshotClient

client = SnapshotClient(
    url="https://your-instance.bioaf.dev",
    experiment_id="EXP-2026-001",
    session_name="clustering-exploration"
)
```

The `session_name` groups related snapshots together. Use descriptive names like "clustering-exploration" or "batch-correction-comparison."

### Taking a Snapshot

After a significant analysis step, capture a snapshot:

```python
import scanpy as sc

# Your analysis work
adata = sc.read_h5ad("filtered_data.h5ad")
sc.pp.normalize_total(adata, target_sum=1e4)
sc.pp.log1p(adata)
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
sc.tl.pca(adata, n_comps=50)
sc.pp.neighbors(adata, n_neighbors=15)
sc.tl.leiden(adata, resolution=0.5)
sc.tl.umap(adata)

# Capture snapshot
client.snapshot(
    adata,
    description="Leiden clustering at resolution 0.5, 15 neighbors",
    tags=["clustering", "leiden"],
    params={"resolution": 0.5, "n_neighbors": 15, "n_top_genes": 2000}
)
```

The SDK automatically extracts from the AnnData object:

- `adata.n_obs` (cell count) and `adata.n_vars` (gene count).
- Cluster labels from `adata.obs["leiden"]` (or the most recent clustering key).
- UMAP coordinates from `adata.obsm["X_umap"]`.
- Top marker genes if `sc.tl.rank_genes_groups` has been run.

The `params` dictionary records your parameter choices for comparison. The `tags` list enables filtering snapshots by topic.

### Taking Multiple Snapshots

The typical workflow involves trying several parameter variations and snapshotting each:

```python
for resolution in [0.3, 0.5, 0.8, 1.0, 1.5]:
    sc.tl.leiden(adata, resolution=resolution, key_added=f"leiden_{resolution}")
    sc.tl.rank_genes_groups(adata, groupby=f"leiden_{resolution}")

    client.snapshot(
        adata,
        description=f"Leiden resolution {resolution}",
        tags=["resolution-sweep"],
        params={"resolution": resolution},
        cluster_key=f"leiden_{resolution}"
    )
```

This creates five snapshots in one session, all tagged "resolution-sweep" for easy comparison.

## R SDK (Seurat)

### Installation

In bioAF-managed RStudio environments, the SDK is pre-installed. For external environments:

```r
remotes::install_github("bioaf/bioaf-r-sdk")
```

### Configuration

```r
library(bioaf)

client <- bioaf_connect(
    url = "https://your-instance.bioaf.dev",
    experiment_id = "EXP-2026-001",
    session_name = "seurat-clustering"
)
```

### Taking a Snapshot

```r
library(Seurat)

# Your analysis work
seurat_obj <- readRDS("filtered_seurat.rds")
seurat_obj <- NormalizeData(seurat_obj)
seurat_obj <- FindVariableFeatures(seurat_obj, nfeatures = 2000)
seurat_obj <- ScaleData(seurat_obj)
seurat_obj <- RunPCA(seurat_obj, npcs = 50)
seurat_obj <- FindNeighbors(seurat_obj, dims = 1:30)
seurat_obj <- FindClusters(seurat_obj, resolution = 0.5)
seurat_obj <- RunUMAP(seurat_obj, dims = 1:30)

# Capture snapshot
bioaf_snapshot(
    client,
    seurat_obj,
    description = "Seurat clustering, resolution 0.5, 30 PCs",
    tags = c("clustering", "seurat"),
    params = list(resolution = 0.5, dims = 30, nfeatures = 2000)
)
```

The R SDK extracts cell/gene counts, cluster assignments from the active identity, UMAP coordinates, and marker genes if `FindAllMarkers` has been run.

## Comparing Snapshots

### In the UI

Navigate to **Experiments > [Experiment] > Analysis > Snapshots**. The snapshot list shows all snapshots across sessions with their descriptions, tags, and timestamps.

To compare snapshots:

1. Select two or more snapshots using the checkboxes.
2. Click "Compare Selected."
3. bioAF displays a side-by-side comparison:
   - **Parameters:** A diff table showing which parameters changed between snapshots.
   - **Cluster counts:** Number of clusters and cells per cluster.
   - **Metrics:** Any custom metrics side by side.
   - **UMAP overlays:** If UMAP coordinates are available, an overlaid visualization showing how cluster assignments differ.

### Via the API

```python
# Fetch snapshots for comparison
snapshots = client.list_snapshots(tags=["resolution-sweep"])

# Compare two snapshots
diff = client.compare(snapshots[0].id, snapshots[2].id)
print(diff.parameter_changes)  # {"resolution": (0.3, 0.8)}
print(diff.cluster_count_change)  # (5, 12)
```

## Starring Snapshots

When you identify the best result from a parameter sweep, star it to mark it as the chosen analysis:

### In the UI

Click the star icon next to any snapshot in the list. Starred snapshots are highlighted in gold and appear at the top of the list.

### Via the SDK

```python
client.star(snapshot_id="snap-abc123")
```

```r
bioaf_star(client, snapshot_id = "snap-abc123")
```

Starring a snapshot adds an entry to the Audit Log recording who selected this result and when. This is valuable for publication provenance: "We selected resolution 0.5 because it produced biologically distinct clusters without over-splitting the T cell compartment."

Only one snapshot per session can be starred. Starring a new snapshot in the same session automatically unstars the previous one.

## Viewing Snapshot History

### By Experiment

**Experiments > [Experiment] > Analysis > Snapshots** shows all snapshots for that experiment, grouped by session.

### By Session

Click on a session name to see only the snapshots from that session in chronological order. This is the best view for reviewing a parameter sweep.

### Across Experiments

For cross-experiment projects, **Projects > [Project] > Snapshots** aggregates snapshots from all constituent experiments, useful for tracking integrated analyses.

## Tips

- Take snapshots frequently during exploratory analysis. They are cheap (small metadata records) and you cannot go back to capture one retroactively.
- Use consistent tag conventions within your team. For example, always tag resolution sweeps as "resolution-sweep" so team members can find each other's explorations.
- Include a clear description with each snapshot. "Resolution 0.5" is less useful than "Resolution 0.5 -- 12 clusters, clean separation of T cell subtypes."
- Star your final chosen snapshot before publishing or presenting results. This creates an auditable record of your analytical decision.
- Export snapshot summaries for publication methods sections via the API (`GET /api/v1/snapshots/{id}/summary`). This provides a structured record of exactly which parameters produced your published results.
