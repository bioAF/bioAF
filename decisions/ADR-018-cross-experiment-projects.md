# ADR-018: Cross-Experiment Analysis Projects

**Status:** Accepted
**Date:** 2026-03-06
**Deciders:** Brent (product owner)

## Context

bioAF's data model is experiment-centric by design (ADR-006). Every pipeline run, notebook session, file, and visualization links back to a single experiment. This is correct for the upstream workflow: bench scientists register experiments, upload FASTQs, and run alignment pipelines — all of which are inherently single-experiment.

But downstream analysis — the work computational biologists spend most of their time on — frequently spans multiple experiments. Common scenarios:

1. **Integration studies.** "Combine tumor samples from experiment 12 with healthy controls from experiment 7 to build a comparative atlas." This is how most cell atlases are built.
2. **Longitudinal analysis.** "Compare the Day 0, Day 7, and Day 30 timepoint experiments to track differentiation." Each timepoint may be a separate experiment because they were sequenced months apart.
3. **Meta-analysis.** "Pool all six experiments from this quarter to increase statistical power for rare cell type detection."
4. **Cross-project comparison.** "How do the germ cell populations in our fertility dataset compare to the published reference from experiment 3?"

In all these cases, the computational biologist needs to select samples from multiple experiments, feed them into a shared analysis, and track the results. bioAF's current `projects` table exists but is purely organizational — a label that groups experiments. It doesn't support pipeline runs, notebook sessions, or provenance tracking at the project level.

The consequence: when Sarah integrates data from three experiments in a notebook, bioAF can only link that notebook session to *one* experiment. The provenance chain for the other two experiments is broken. And when it's time to publish, the GEO submission (ADR-014) can only export one experiment at a time — there's no concept of a multi-experiment submission.

## Decision

Elevate `projects` from an organizational label to a first-class analytical entity. A project can reference samples from multiple experiments and has its own pipeline runs, notebook sessions, analysis snapshots, and provenance chain. The existing experiment-centric model is preserved — projects are an additional layer, not a replacement.

### Data Model Changes

**Upgrade the existing `projects` table:**

```sql
-- Add columns to existing projects table
ALTER TABLE projects ADD COLUMN status VARCHAR(50) DEFAULT 'active';
ALTER TABLE projects ADD COLUMN hypothesis TEXT;
ALTER TABLE projects ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
```

**New linkage table — project ↔ samples (not experiments):**

```sql
project_samples (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    added_by_user_id INTEGER REFERENCES users(id),
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT,                            -- why this sample was included
    PRIMARY KEY (project_id, sample_id)
)
```

The linkage is at the *sample* level, not the experiment level. This is deliberate: Sarah rarely wants "all samples from experiment 7." She wants "the 4 healthy control samples from experiment 7 and the 8 tumor samples from experiment 12." Sample-level selection gives her that precision.

The parent experiments are always derivable from the selected samples (`samples.experiment_id`), so no information is lost.

**Extend existing tables with optional project references:**

```sql
-- Pipeline runs can now belong to a project instead of (or in addition to) an experiment
ALTER TABLE pipeline_runs ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Compute sessions can now belong to a project
ALTER TABLE compute_sessions ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Analysis snapshots (ADR-015) can now belong to a project
ALTER TABLE analysis_snapshots ADD COLUMN project_id INTEGER REFERENCES projects(id);

-- Files/results can be linked to a project
ALTER TABLE files ADD COLUMN project_id INTEGER REFERENCES projects(id);
```

The `experiment_id` columns remain and are not made nullable — existing single-experiment workflows are unchanged. For cross-experiment work, `project_id` is set *in addition to* or *instead of* `experiment_id`. The API accepts either or both.

### Provenance: From Tree to DAG

With projects, provenance becomes a directed acyclic graph:

```text
Experiment 7          Experiment 12
    │                     │
    ├── Sample A          ├── Sample E
    ├── Sample B          ├── Sample F
    └── Sample C          ├── Sample G
         │                └── Sample H
         │                     │
         └────────┬────────────┘
                  │
           Project: "GBM Atlas"
                  │
           ┌──────┴──────┐
           │              │
      Pipeline Run    Notebook Session
      (integration)   (annotation)
           │              │
        h5ad output    Snapshots
           │
       cellxgene pub
```

Multiple experiments feed into a single project. The project has its own pipeline runs and notebook sessions. Outputs trace back to all contributing samples and their parent experiments.

The provenance view (F-072) must render this as a DAG, not a tree. The implementation approach: the provenance renderer already walks foreign keys to build the graph. Adding `project_id` to pipeline runs, notebook sessions, and files simply adds more edges. The renderer treats any entity with multiple parent paths as a merge node.

### UI Integration

**Project creation:**

Projects are created from the existing Projects page or from the dataset browser. The scientist selects samples from one or more experiments using the dataset browser's existing filters (organism, tissue, batch, experiment, QC status) and adds them to a new or existing project. The selection UI is a multi-select table with checkboxes — not a new paradigm.

**Project detail page:**

Mirrors the experiment detail page structure but with a different data scope:

- **Samples tab:** All samples in the project, grouped by source experiment. Shows which experiment each sample came from.
- **Data tab:** Files linked to the project (inputs selected from experiments + outputs produced by project-level pipeline runs).
- **Pipeline Runs tab:** Runs that were launched with this project's sample set as input.
- **Analysis tab:** Notebook sessions and snapshots (ADR-015/016) linked to this project.
- **Provenance tab:** DAG view showing the full lineage from source experiments through the project's analysis.

**Launching from a project:**

When a pipeline or notebook is launched from a project detail page:

- The input data selector is pre-filtered to the project's samples.
- The resulting pipeline run / notebook session is linked to the project.
- If the project spans experiments, the provenance links propagate to all source experiments.

### GEO Export Interaction

ADR-014 (GEO Export) must be updated to support project-level export. A cross-experiment publication typically submits to GEO as a single "SuperSeries" that references multiple sub-Series (one per experiment). The GEO export service generates:

- One metadata spreadsheet per source experiment (sub-Series)
- A SuperSeries metadata file that links them together
- A unified file manifest covering all experiments

This is a natural extension of the single-experiment export — it runs the same export logic per experiment and wraps the results in GEO's SuperSeries structure.

### What a Project Is NOT

A project is not a replacement for experiments. The experiment remains the unit of bench science: it's where samples are registered, FASTQs are uploaded, and initial QC pipelines are run. A project is a *downstream analytical construct* that draws from completed experiments. The typical lifecycle is:

1. Priya registers experiments and samples.
2. Jake uploads FASTQs and runs alignment pipelines (experiment-level).
3. Sarah creates a project, selects samples from one or more experiments, and performs integrative analysis (project-level).

This mirrors how computational biology actually works: upstream processing is per-experiment, downstream analysis is cross-experiment.

## Rationale

- **Sample-level linkage, not experiment-level.** Scientists almost never want all samples from an experiment in a cross-experiment analysis. They want specific subsets. Sample-level selection is more work to build but dramatically more useful.
- **Additive, not breaking.** Adding `project_id` columns alongside existing `experiment_id` columns means every existing workflow, API endpoint, and UI view continues to work unchanged. Projects are opt-in.
- **Experiments stay as the upstream unit.** Forcing cross-experiment concepts into the experiment model would create confusion: is this "experiment" a real wet-lab experiment or an analytical construct? Keeping them separate preserves the semantic clarity that bench scientists depend on.
- **DAG provenance is inevitable.** Even without projects, provenance becomes a DAG the moment a notebook reads files from two different pipeline runs. Projects make this explicit and trackable rather than implicit and invisible.
- **GEO SuperSeries is the natural export target.** GEO already has a concept for multi-experiment submissions. Aligning bioAF's project model with GEO's SuperSeries means the export path is clean.

## Consequences

- The `projects` table is upgraded with new columns (non-breaking migration — all new columns are nullable).
- The `project_samples` linkage table is added.
- `pipeline_runs`, `compute_sessions`, `analysis_snapshots`, and `files` gain an optional `project_id` column.
- The provenance view (F-072) must be updated to render DAGs. This is the most significant UI change. Libraries like `dagre` or `d3-dag` can handle the layout.
- The dataset browser (F-011) gains a "Add to project" action on selected samples.
- The pipeline launcher (F-030) and notebook launcher (F-040, F-041) gain a project context option alongside the existing experiment context.
- The GEO export service (ADR-014) is extended with SuperSeries support. This can be deferred until after the single-experiment export is working.
- The audit log records project-level actions: sample addition/removal, pipeline launches, notebook sessions.
- Navigation is updated: the Projects section in the sidebar becomes a first-class destination alongside Experiments, with its own list, detail, and provenance views.
