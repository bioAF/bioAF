### Quality control

- QC dashboards now analyze ChIP-seq and ATAC-seq pipeline runs, reporting peak counts,
  FRiP, and (for ChIP-seq) NSC/RSC strand cross-correlation, plus TSS enrichment for
  ATAC-seq. Bulk RNA-seq QC coverage is more thorough as well.

### Provenance

- Provenance reports can now be exported as CSV, in addition to JSON and PDF, from
  projects, experiments, and pipeline runs.

### Fixes

- Peak-calling pipelines (ChIP-seq, ATAC-seq) now launch correctly: the selected
  reference genome is passed through to nf-core and the MACS genome size is derived
  automatically, so runs no longer fail on a missing `--genome` or `--macs_gsize`.
- Importing a multi-run SRA/ENA study via fetchngs no longer crashes data ingest, and
  fetched FASTQ files now attach to their samples.
- Fixed a cluster cost leak where Kubernetes system pods could pin the pipelines node
  pool open and keep it from scaling to zero when idle; the pool is now tainted so it
  releases when unused.
- Literature full-text retrieval now resolves articles through Europe PMC, and a
  text-sanitizer bug that could strip article prose has been fixed.

### Coming soon

- Groundwork for an upcoming literature validation feature.
