### Quality control

- Corrected the read count and sample count shown on QC dashboards. Both were
  derived by counting the quality-report entries a run produced, but sequencers
  write one entry per file rather than per sample: paired-end runs produce two
  files per sample, and a sample split across lanes produces more still. A
  single-cell run of one sample across four files was reporting four samples,
  and either double or half its true read depth depending on the pipeline.
  bioAF now works out the real samples from the alignment step and adds up each
  sample's files correctly.

  What changes when you regenerate a dashboard: single-cell runs show the
  correct read depth and sample count, and paired-end ChIP-seq and ATAC-seq runs
  show the correct sample count. Read depth on bulk RNA-seq, ChIP-seq and
  ATAC-seq runs is unchanged, as are all other metrics. Existing dashboards keep
  their current numbers until regenerated.

  This also matters for literature validation, where a paper's reported reads
  per sample is compared against the run: on single-cell papers that comparison
  was previously made against a figure that counted every read twice.
