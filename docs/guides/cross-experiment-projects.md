# Cross-Experiment Projects

bioAF's data model is experiment-centric by design, but downstream analysis frequently spans multiple experiments. Cross-experiment projects let you group experiments, run pipelines across their combined samples, and maintain full provenance through a DAG (directed acyclic graph) that traces every output back to its source experiments.

## When to Use Projects

Cross-experiment projects are appropriate when:

- **Integration studies:** You are combining samples from multiple experiments to build a comparative atlas (e.g., tumor samples from experiment 12 with healthy controls from experiment 7).
- **Longitudinal analysis:** Comparing timepoints from separate experiments (Day 0, Day 7, Day 30) that were sequenced months apart.
- **Meta-analysis:** Pooling experiments from an entire quarter to increase statistical power for rare cell type detection.
- **Multi-condition comparisons:** Drug treatment vs control vs untreated, each run as separate experiments for bench workflow reasons.

Do not use projects for work that belongs within a single experiment. If all samples were collected and sequenced together, keep them in one experiment.

## Creating a Project

### Step 1: Navigate to Projects

Go to **Projects > Create New Project**.

### Step 2: Define Project Metadata

- **Project name:** A descriptive name (e.g., "Q1 2026 Tumor vs Healthy Atlas").
- **Description:** Explain the scientific goal and which experiments are involved.
- **Lead analyst:** The computational biologist responsible for the project. This person receives notifications for project-level events.

### Step 3: Add Experiments

Click "Add Experiments" and select from the list of available experiments. You can search by experiment name, ID, or status. Only experiments in `analysis` or `complete` status can be added to projects (upstream processing must be finished).

For each added experiment, you see:

- Experiment name and ID.
- Number of samples.
- Available data types (FASTQs, count matrices, processed outputs).
- Status and last activity date.

### Step 4: Save

Click "Create Project." The project is created and you are taken to the project overview page.

## Adding Samples to a Project

By default, adding an experiment to a project includes all of its samples. You can refine this.

### Including All Samples

This is the default. All samples from added experiments are available for project-level pipelines and analyses.

### Selecting Specific Samples

From the project overview, click **Samples > Edit Selection**. For each experiment, toggle individual samples on or off. Common reasons to exclude samples:

- QC failures identified during pipeline review.
- Samples from conditions not relevant to this project's analysis.
- Duplicate or replicate samples that should not be double-counted.

### Adding Samples Later

When new experiments complete processing, add them to the project via **Projects > [Project] > Add Experiments**. Existing pipeline runs and analyses are not affected. New runs will see the updated sample set.

## Running Pipelines in Project Context

### Launching a Project-Level Pipeline

From the project overview, click **Pipelines > Launch Pipeline**. The pipeline launcher works the same as for single experiments, with key differences:

- **Input files** are drawn from all experiments in the project. The file browser shows files grouped by experiment.
- **Sample metadata** is merged across experiments. If experiments use different metadata schemas, bioAF displays a union of all fields with blanks where a field is not applicable.
- **Output files** are stored in the project's dedicated GCS path (`gs://<instance>-projects/<project-id>/`), separate from individual experiment outputs.

### Common Project-Level Pipelines

- **Seurat/Scanpy integration:** Merge count matrices from multiple experiments, perform batch correction (Harmony, scVI, BBKNN), and cluster the integrated dataset.
- **Differential expression across conditions:** Compare gene expression between experimental conditions that were run as separate experiments.
- **Reference mapping:** Map cells from new experiments against an existing reference atlas.

### Pipeline Parameters

When launching a project-level pipeline, some parameters require special attention:

- **Batch variable:** Specify which metadata field identifies the batch (usually experiment ID or sequencing run). This is critical for batch correction.
- **Sample grouping:** Define how samples should be grouped for comparison (e.g., treatment vs control).
- **Reference version:** Ensure all experiments were processed against the same Reference Dataset version. If not, consider reprocessing with a consistent reference before integration.

## DAG Provenance

Every pipeline run, file, and analysis snapshot in a project is tracked in a directed acyclic graph (DAG) that records the full provenance chain.

### Understanding the DAG

The project DAG connects:

- **Source experiments** as root nodes.
- **Samples** as children of their parent experiments.
- **Input files** (FASTQs, count matrices) linked to their samples.
- **Pipeline runs** as processing nodes, with edges from input files to output files.
- **Analysis snapshots** linked to the pipeline outputs and Notebook Sessions that produced them.

This graph lets you trace any result (a figure, a gene list, a cluster annotation) back through the exact processing steps to the original tissue samples.

### Viewing the DAG

Navigate to **Projects > [Project] > Provenance**. The DAG is displayed as an interactive graph:

- Click any node to see its details (file metadata, pipeline parameters, snapshot description).
- Hover over edges to see the transformation applied.
- Use the filter bar to show only specific data types or pipeline steps.
- Toggle between "full DAG" (all nodes) and "simplified DAG" (only major processing steps).

### Exporting the DAG

For publication, export the DAG as:

- **JSON:** Machine-readable format for supplementary data.
- **SVG/PNG:** Visual format for figures or presentations.
- **Text summary:** A narrative description of the processing chain, suitable for methods sections.

Navigate to **Projects > [Project] > Provenance > Export** and select your preferred format.

## Managing Projects

### Project Status

Projects have simple statuses:

- **Active:** Work in progress. Pipelines can be launched and analyses created.
- **Archived:** Work is complete. The project is read-only. Archived projects can be reactivated if needed.

### Removing Experiments

To remove an experiment from a project, navigate to **Projects > [Project] > Experiments** and click "Remove" next to the experiment. This does not delete any data; it only removes the association. Pipeline runs that already used data from the removed experiment retain their provenance links.

### Deleting Projects

Projects can only be deleted if they have no pipeline runs or analysis snapshots. If the project has any work products, archive it instead. This policy protects against accidental loss of provenance information.

## Tips

- Name projects with the scientific question, not just a date or experiment list. "Tumor Microenvironment Atlas Q1 2026" is more findable than "Project 47."
- Add a description that explains the rationale for combining these specific experiments. Future team members (or your future self) will thank you.
- Before running integration pipelines, verify that all constituent experiments used compatible library preparation protocols and were processed against the same reference genome version. Incompatible inputs produce misleading integration results.
- Use the DAG export for methods sections in publications. Reviewers and readers increasingly expect detailed provenance for computational analyses.
- When a project grows beyond 10 experiments, consider splitting it into sub-projects with a parent SuperSeries for GEO export. This keeps the DAG manageable and aligns with GEO's submission structure.
- Archive completed projects promptly. This prevents accidental modifications and signals to the team that the analysis is finalized.
