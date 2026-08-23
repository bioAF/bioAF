### Literature validation

- Give bioAF a paper and it will try to reproduce the paper's findings from the
  authors' own deposited data, so you can judge whether a paper of unknown
  quality is worth a deeper look before committing a project to it. bioAF reads
  the methods, identifies the assay, fetches the deposited data, runs it through
  the matching nf-core pipeline on your infrastructure, and reports the paper's
  numbers against the measured ones. You do not pick a pipeline; bioAF chooses
  one from the methods.

  Works today on **bulk RNA-seq** papers (gene expression between conditions,
  reporting differentially expressed genes), **ChIP-seq** papers (protein binding
  or histone marks, reporting peaks), and **ATAC-seq** papers (open chromatin,
  reporting peaks or differentially accessible regions).

  Two things about the paper matter more than the assay. It must state a data
  accession in GEO or SRA, or there is nothing to re-run. And it should include
  the authors' own result table, their gene or peak list, usually supplementary:
  with it bioAF compares finding against finding, without it you learn only
  whether the deposited data is sound.

  Verdicts are suggestions for a person to ratify, not automatic judgements.
  `inconclusive` is a normal result and means the evidence did not settle the
  question.

- **Single-cell RNA-seq papers are now accepted, and the path is still in
  progress.** It has not yet been proven end to end, and how far a study gets
  depends on the paper. Expect difficulty when the study has fewer than two
  samples per condition (refused outright, the statistics need replicates), when
  the data is older 10x from roughly pre-2019 (often deposited as aligned BAMs,
  or in a read layout needing conversion), or when samples were pooled across
  donors (needs per-donor labels).

- A reproduction step that fails or cannot run no longer discards the study. The
  paper is still assessed on its data, and the study states which step did not
  run and why.

- A comparison with fewer than two samples in either arm is now refused at the
  gate and before launch, naming the arm and its count, instead of failing
  partway through a run.

- A difference caused by the authors' tools rather than their data is now
  explained by name instead of counting against the paper. A cell count that
  differs because they used CellRanger and bioAF used STARsolo is reported as a
  known difference between two tools.

- The reproduction step now names its input file, and the ChIP-seq and ATAC-seq
  routes select that file deterministically rather than picking among
  equally-named candidates.

### Pipelines

- New **10x bamtofastq** pipeline, which rebuilds the original FASTQ reads from
  an aligned 10x BAM. Older single-cell datasets are often deposited as BAMs,
  where the cell barcode survives only in the file's tags, so a general-purpose
  converter returns reads with no barcode. An administrator builds the template
  once under Pipelines > Pipeline Templates before its first run.

### Settings

- Literature validation moves to open beta. Any administrator can now enable it
  under Settings > Beta Features. It remains off by default.
