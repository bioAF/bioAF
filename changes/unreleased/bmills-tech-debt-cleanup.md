### Pipeline runs

- Fix a cross-sample file-contamination bug: when a selected sample had no
  linked input files, the launch used to back-fill every such sample with the
  entire experiment's FASTQ list, so one sample's process received another
  sample's reads (and duplicate renamed + raw copies). On large data this ran
  for hours before the job was killed. FASTQ-consuming pipelines (nf-core,
  rnaseq/scrnaseq) now reject samples with no input files and report exactly
  which samples are missing them.
- When some selected samples have no files, launching (and reproducing) a run
  now offers to drop those samples and continue with the rest, instead of
  failing outright.
- The Provenance tab on a Pipeline Run now lists each input file by project,
  experiment, sample, and filename instead of showing bare numeric file IDs.
- A previous pipeline run's output files are no longer fed back in as inputs to
  the next run. Run inputs now use raw uploads only by default; a new
  "include derived inputs" option opts back in. This stops the dataset from
  compounding every run (which had been causing runs to balloon and get killed).
- Pipeline output files are now associated with the specific sample they belong
  to, instead of being linked to every sample in the run. Aggregate outputs
  (e.g. MultiQC) that don't belong to one sample are still linked to all of them.

### API

- Service and adapter errors now use a typed domain-exception hierarchy with a
  structured `{detail, code}` response envelope (some errors also include a
  `details` object). As part of this, several internal (non-`/v1/integrations`)
  endpoints now return `404` or `409` where they previously returned a generic
  `400`, when that status is the semantically correct one (not found, invalid
  state, or conflict/quota). The public Integration API surface is unchanged.

### Documentation

- Aligned README and `docs/` terminology with the canonical glossary (Agent
  Review, Work Nodes, Notebook Sessions, QC Dashboards, Audit Log, Reference
  Dataset, Naming Profile, Segment, and related terms).

### Internal

- Consolidated frontend status-badge rendering into a single entity-keyed
  registry (no visual change).
- Removed verified dead code and stale phase-era mock-data and backfill scripts.
