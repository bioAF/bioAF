# ADR-016: Snapshot Comparison UI

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

ADR-015 introduces the Analysis Snapshot SDK, which captures structured metadata from AnnData and Seurat objects at scientist-defined checkpoints. Those snapshots are stored in PostgreSQL and accessible via API. But snapshots in a database are only useful if scientists can see them, compare them, and use them to make decisions.

The core use case is parametric comparison: "I tried three clustering resolutions, which one produced the most biologically meaningful clusters?" This is something computational biologists do constantly but currently manage by visual memory, screenshots, or notes scribbled in notebook markdown cells.

bioAF already has a precedent for this pattern: pipeline run comparison (F-032) supports side-by-side parameter diffs between pipeline runs. Snapshot comparison extends this concept into the interactive analysis layer.

## Decision

bioAF provides a Snapshot Comparison UI that lets scientists view, diff, and compare analysis snapshots within an experiment. The UI is accessible from the experiment detail page and from within notebook sessions (via a bioAF widget or a browser tab).

### Snapshot Timeline View

The primary entry point is a chronological timeline of all snapshots for an experiment, grouped by notebook session.

```text
Experiment: "GBM_atlas_v2"

Session 1 (Sarah, Mar 5, Jupyter) ──────────────────────────
  ● leiden_0.8_no_correction       8,432 cells  │ 14 clusters
  ● leiden_0.5_no_correction       8,432 cells  │ 9 clusters
  ● leiden_0.5_harmony_corrected   8,430 cells  │ 11 clusters
  ★ leiden_0.3_scvi_corrected      8,430 cells  │ 7 clusters    ← starred

Session 2 (Jake, Mar 6, RStudio) ────────────────────────────
  ● sct_leiden_0.5                 8,102 cells  │ 8 clusters
  ● sct_leiden_0.3                 8,102 cells  │ 6 clusters
```

Each snapshot shows the label, cell/gene counts, cluster count, and timestamp. Scientists can star snapshots to mark them as "chosen" or "final."

### Side-by-Side Comparison View

Selecting two or more snapshots opens a comparison view. The comparison is structured as a diff.

**Parameter diff:**

The system computes a structured diff between the `parameters_json` fields. Only changed parameters are highlighted.

```text
                          Snapshot A                    Snapshot B
                          leiden_0.5_no_correction      leiden_0.5_harmony_corrected
─────────────────────────────────────────────────────────────────────────
neighbors.n_neighbors     15                            15
leiden.resolution         0.5                           0.5
batch_correction: harmony (theta=2.0)    ← NEW
cell_count                8,432                         8,430                  ← CHANGED
cluster_count             9                             11                     ← CHANGED
embeddings                [X_pca, X_umap]               [X_pca, X_harmony,    ← CHANGED
                                                         X_umap]
```

**Cluster distribution comparison:**

A visual comparison of cluster sizes, shown as a grouped bar chart or a Sankey diagram (if cell overlap can be inferred from checkpoint data). This helps answer "did adding batch correction split one big cluster into two small ones, or did it rearrange cells entirely?"

```text
Cluster sizes:
           Snapshot A    Snapshot B
Cluster 0  1,200         890
Cluster 1    980         970
Cluster 2    850         850
Cluster 3    720         680
...
NEW →                    Cluster 9: 340
NEW →                    Cluster 10: 210
```

**Figure comparison (if figures were attached):**

Side-by-side display of figures saved with each snapshot. Typically UMAPs, which are the primary visual comparison tool in scRNA-seq analysis. The UI supports synchronized zoom/pan across paired figures.

**Command log diff (Seurat only):**

For Seurat snapshots, the `@commands` log provides an ordered list of every function called. The diff shows which operations were added, removed, or changed between snapshots.

```text
Snapshot A                              Snapshot B
─────────────────────────────────────────────────────────
NormalizeData (RNA)                     NormalizeData (RNA)
FindVariableFeatures (RNA, 2000)        FindVariableFeatures (RNA, 2000)
ScaleData (RNA)                         ScaleData (RNA)
RunPCA (RNA, npcs=50)                   RunPCA (RNA, npcs=50)
                                      + RunHarmony (theta=2.0, group="batch")   ← NEW
FindNeighbors (pca, dims=1:30)          FindNeighbors (harmony, dims=1:30)      ← CHANGED
FindClusters (res=0.5)                  FindClusters (res=0.5)
RunUMAP (pca, dims=1:30)               RunUMAP (harmony, dims=1:30)            ← CHANGED
```

### Multi-Snapshot Comparison Table

For comparing more than two snapshots (common when sweeping a parameter), the UI provides a table view:

```text
Label                    Resolution  Correction  Cells   Clusters  Notes
leiden_0.8_none          0.8: 8,432   14        Over-clustered
leiden_0.5_none          0.5: 8,432   9
leiden_0.5_harmony       0.5         Harmony     8,430   11        Batch 2 splits oddly
★ leiden_0.3_scvi        0.3         scVI        8,430   7         Clean separation
```

Sortable by any column. The starred snapshot is highlighted.

### Provenance Integration

Snapshots appear in the experiment's provenance view (F-072) and audit timeline (F-073):

- The provenance chain extends: experiment → samples → pipeline run → outputs → **snapshot sequence** → final figures
- Starred ("chosen") snapshots are flagged in the provenance view as decision points
- The snapshot comparison is exportable as a PDF or HTML report for inclusion in publication supplementary materials

### API Endpoints

```text
GET  /api/v1/experiments/{id}/snapshots
     → List all snapshots for an experiment, grouped by session

GET  /api/v1/snapshots/{id}
     → Single snapshot detail

POST /api/v1/snapshots/{id}/star
     → Toggle star status on a snapshot

GET  /api/v1/snapshots/compare?ids=1,2,3
     → Structured diff between selected snapshots

GET  /api/v1/snapshots/compare?ids=1,2,3&format=pdf
     → Exportable comparison report
```

### UI Locations

The snapshot comparison UI is accessible from three places:

1. **Experiment detail page → Analysis tab.** Shows the full snapshot timeline for the experiment. This is the primary entry point.
2. **Notebook session detail.** Shows snapshots from a specific session only, with a link to the broader experiment view.
3. **Provenance view.** Snapshots appear as nodes in the provenance graph. Clicking a snapshot opens its detail or comparison view.

## Rationale

- **The diff is the value, not the snapshot itself.** A single snapshot in isolation is marginally useful (it records what happened). The diff between two snapshots is where the scientific insight lives: it answers "what did I change, and what effect did it have?" The UI must make diffing the primary interaction, not an afterthought.
- **Timeline grouped by session reflects how scientists think.** A scientist doesn't think "show me snapshot #47." They think "show me what I tried on Tuesday afternoon." Grouping by notebook session maps to natural memory.
- **Starring marks decisions.** The iterative exploration produces many snapshots, but only one (or a few) represent the chosen approach. Starring makes this explicit and provides a filtered view for publication and provenance.
- **Command log diff is uniquely powerful for Seurat.** No other tool surfaces Seurat's `@commands` slot as a diffable record. This alone could drive adoption among R-focused bioinformaticians, because it gives them something they've never had: a concrete answer to "what exactly did I do differently between these two attempts?"
- **PDF/HTML export serves the publication workflow.** Reviewers and collaborators need to see the comparison outside the bioAF UI. An exportable report (parameter table + figures + notes) can go directly into a paper's supplementary materials or a methods section.
- **Existing pipeline comparison (F-032) validates the pattern.** bioAF already provides side-by-side parameter comparison for pipeline runs. The snapshot comparison extends the same concept to interactive analysis, using the same UI patterns where possible.

## Consequences

- The experiment detail page gains a new "Analysis" tab (or the existing analysis section is expanded) to house the snapshot timeline.
- The comparison view is a new React component. It should reuse the parameter diff component from pipeline run comparison (F-032) where possible.
- The PDF/HTML export requires a server-side rendering step (or a client-side library like html2pdf). This can be deferred to a follow-up if needed; the in-app comparison view is the MVP.
- The figure comparison feature depends on scientists attaching figures to snapshots (`bioaf.snapshot(adata, figure=plt.gcf())`). If adoption of figure attachment is low, the comparison view still works based on parameter and cluster diffs alone.
- The Sankey diagram for cluster-to-cluster cell mapping requires checkpoint data (`save_checkpoint=True`), which stores the obs DataFrame. This is an advanced feature; the basic cluster count comparison works without checkpoints.
- This ADR depends on ADR-015 (Analysis Snapshot SDK). The SDK must be implemented first; the comparison UI consumes the data the SDK produces.
