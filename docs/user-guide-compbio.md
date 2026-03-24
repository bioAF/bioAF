# Computational Biologist User Guide

This guide covers the bioAF workflow for computational biologists who run pipelines, manage notebooks, and analyze data.

## Getting Started

Log in with your credentials. Your account has the **comp_bio** role, giving you access to pipelines, notebooks, and environments.

## Pipeline Catalog

Navigate to **Pipeline Catalog** to browse available pipelines:

- **nf-core/scrnaseq** - Single-cell RNA-seq processing
- **nf-core/rnaseq** - Bulk RNA-seq alignment and quantification
- **nf-core/atacseq** - ATAC-seq analysis
- Custom pipelines added by your admin

Each pipeline shows its description, required inputs, and default parameters.

## Running Pipelines

1. Select a pipeline from the catalog
2. Click **Launch**
3. Configure parameters:
   - Select the experiment and samples
   - Set compute resources (CPUs, memory)
   - Override default parameters as needed
4. Click **Submit**

Monitor runs from **Pipeline Runs**:

- View real-time progress and logs
- See per-stage completion status
- Access output files when complete

## Notebook Sessions

Navigate to **Workbench > Notebooks** to manage notebook sessions:

1. Click **Launch Notebook**
2. Select environment type: JupyterHub or RStudio
3. Choose compute resources
4. Select a versioned environment and image version

Sessions auto-stop after idle timeout (configurable). You'll receive a notification when a session is about to stop.

## Environments

Navigate to **Workbench > Environments** to manage versioned compute environments:

- **Create** a named environment with a description and visibility (team or organization)
- **Add versions** by uploading a Dockerfile or conda environment.yml definition
- **Build** a version to produce a container image via Cloud Build
- **Select** a built environment version when launching notebook sessions

Each environment can have multiple versions, letting you iterate on dependencies while keeping previous versions available. Only versions with a "ready" status (successful build) can be used for notebook launches.

## Data Management

Navigate to **Data & Files** for file management:

- Browse uploaded files and pipeline outputs
- Download individual files or datasets
- Organize files by experiment and project

## Results and Visualization

From **Results**:

- View QC dashboards with quality metrics per pipeline run
- Launch cellxgene for interactive single-cell exploration
- Browse publication-quality plots
- Search across all results with the global search

## Notebook Templates

Access pre-configured analysis templates from **Templates**:

- Standard analysis workflows
- Customizable parameters
- Auto-populated with your experiment data

## Tips

- Use the **Dashboard** to see active pipeline runs and notebook sessions
- Check **Activity Feed** for team-wide events
- Set notification preferences for pipeline completions and failures
