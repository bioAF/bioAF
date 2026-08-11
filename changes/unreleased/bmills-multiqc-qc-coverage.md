### Quality control

- QC dashboards now work for every pipeline, not just the four bioAF had been
  taught individually. Previously, running anything outside single-cell RNA-seq,
  bulk RNA-seq, ChIP-seq or ATAC-seq produced a dashboard with no numbers on it,
  because nobody had written a reader for that pipeline's output yet. bioAF now
  reads the standard quality report that every nf-core pipeline produces, so a
  methylation, variant-calling or any other pipeline you install shows real
  metrics straight away, with no work needed on our side first.

- Quality dashboards for these newly covered pipelines also list every other
  measurement their tools reported, labelled with the tool that produced it, so
  nothing a pipeline measured is thrown away just because bioAF does not have a
  standard name for it yet.

### Fixes

- Fixed missing sequencing numbers on single-cell RNA-seq quality dashboards.
  Read count, sample count, GC content, read length and duplication were blank
  on runs processed with recent versions of the underlying reporting tool, which
  had changed the layout of the file bioAF reads them from. The cell-level
  numbers on those dashboards (cell count, genes per cell, saturation, mapping
  rates) were never affected. Regenerating a run's QC dashboard fills the missing
  values in.

- A pipeline bioAF has no tailored quality template for is no longer read as
  though it were a single-cell run, which previously applied single-cell
  expectations, chart choices and quality wording to unrelated pipelines.
