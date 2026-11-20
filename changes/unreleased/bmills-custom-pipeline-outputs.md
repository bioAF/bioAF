### Custom pipelines

- Fix custom pipeline runs that finished as completed but never saved their
  outputs. When the results bucket was not set directly (for example, when it
  is derived from a `bioaf-raw-<name>` raw bucket), the run skipped the upload
  while the run page looked for outputs in the derived bucket. Runs now upload
  to the same output location the run page collects from, for experiment and
  project runs alike. This does not restore outputs from earlier runs that were
  never uploaded; relaunch those runs.
- A custom pipeline launch on a GCS or S3 install with no results bucket is now
  refused before anything is submitted or recorded, instead of starting a run
  whose outputs would be lost.
- If a run's outputs cannot be uploaded, the run fails with "pipeline outputs
  could not be copied to ..." rather than completing without them. A pipeline
  that had already failed keeps its own exit status.
- The custom QC dashboard now finds `qc_metrics.json` for project-scoped runs.
