# Bench Scientist User Guide

Use bioAF to register experiments, manage Samples, associate files and review results. Your built-in or
custom role determines which actions are available; enabled components also affect navigation.

## Register an Experiment

1. Open **Experiments > Experiment List** and choose **New Experiment**.
2. Enter a name and description, select an Experiment Template when applicable, and optionally select a Project.
3. Review the template's required metadata and create the experiment.

The normal lifecycle is `registered` -> `library_prep` -> `sequencing` -> `fastq_uploaded` -> `processing` ->
`pipeline_complete` -> [`reviewed` ->] `analysis` -> `complete`. The scientist's review/handoff is advisory,
not a data-access or pipeline-launch gate; an affirmative review can record the `reviewed` status.
Only authorized actions and services move status; a file appearing does not imply an analysis is complete.

## Manage Samples and Batches

From the experiment, add Samples individually or use the CSV/template import workflow. Complete the required
organism, tissue, library and custom metadata for the study. Preview imported rows and correct reported
errors before confirming; inspect row-level failures after confirmation rather than assuming every row saved.

Sample Batches organize sample preparation. Sequencing Batches track sequencing and delivered files. Keep
both associations accurate so downstream sample sheets can use the intended inputs.

## Upload and Associate Files

Open **Data & Files > Data Upload**, choose files and select the relevant experiment or other association
where offered. Check the result in **Data & Files > Files**. Use **Associate** to correct or add supported
associations; files are not automatically assigned to the intended experiment just because you uploaded them.

Experiment filters and association pickers search by experiment name and page through eligible records.
Use their page controls for older experiments. Deleted experiments are excluded from ordinary lists.
Naming Profiles can parse filename metadata, but auto-ingest is currently disabled. See
[Naming Profiles](guides/naming-profiles.md).

## Review Results

Open the experiment's results or **Results > QC Dashboards**, **cellxgene Explorer**, or **Plot Archive**
when those components and permissions are available. Review pipeline status and QC before using outputs.
Agent Review notes are advisory; the scientist's review decision is a separate recorded action.

Download individual registered files, or use [Data Exports](guides/data-exports.md) for an experiment package.
[GEO Export](guides/geo-export.md) creates submission metadata and checksum records; upload data files to
GEO separately.

## Notifications and Lab Knowledge

The header bell shows in-app notifications. Configure preferences from your profile's notification settings.
Email and Slack require administrator configuration. **Lab Knowledge** contains Literature Library, Lab
Documents, Lab Glossary and Scientific Decision Records; available actions follow their grants.
See [Literature and AI Workflows](guides/literature-and-ai.md) before using hosted AI features.
