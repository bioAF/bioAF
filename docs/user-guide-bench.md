# Bench Scientist User Guide

This guide covers the bioAF workflow for bench scientists who register experiments, manage samples, and review results.

## Getting Started

After receiving your invitation email, click the link to set your password and log in. Your account will have the **bench** role.

## Registering an Experiment

1. Navigate to **Projects > Experiment List** in the sidebar
2. Click **New Experiment**
3. Fill in experiment details:
   - Name (e.g., "PBMC scRNA-seq Batch 3")
   - Description
   - Select an experiment template (pre-configured by your admin)
   - Project assignment (optional)
4. Click **Create**

Your experiment starts in the **registered** status and progresses through: library_prep, sequencing, fastq_uploaded, processing, analysis, complete.

## Managing Samples

From your experiment detail page:

1. Click **Add Samples** or use batch upload
2. For each sample, fill in MINSEQE-compliant metadata:
   - Sample name, organism, tissue type
   - Library strategy (e.g., scRNA-seq, ATAC-seq)
   - Custom fields defined by your experiment template
3. Samples can be organized into **batches** for processing

## Uploading Data

1. Navigate to **Data & Files > Upload** in the sidebar
2. Select your FASTQ or other data files
3. Files are automatically associated with your experiment
4. Upload progress is shown in real-time

## Viewing Results

When your experiment progresses to the analysis or complete stage:

1. Navigate to **Results** in the sidebar
2. View QC dashboards with quality metrics
3. Access cellxgene for interactive single-cell data exploration
4. Download result files and plots

## Notifications

You'll receive notifications for:

- Pipeline completion on your experiments
- QC results ready
- Experiment status changes

Click the bell icon in the header to see recent notifications. Configure your notification preferences in **Profile > Notifications**.

## Quick Tips

- The **Dashboard** shows your experiments at a glance
- Contact your admin if you need access to additional components
