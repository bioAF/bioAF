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
- On an install whose storage is NFS, a custom pipeline launch is refused before
  anything is submitted. Pipeline jobs do not have NFS mounted, so their outputs
  could not be saved.
- QC dashboards now read metrics and plots from the run's own output location.
  Project-scoped custom runs get their QC metrics and plots, whichever QC
  template they use, and installs with no results bucket setting no longer show
  an empty QC dashboard for runs whose outputs were saved.
