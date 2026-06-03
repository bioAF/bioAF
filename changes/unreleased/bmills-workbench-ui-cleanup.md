### Workbench

- Collapse the Work Node launcher from a 6-step wizard into a single
  Configure step plus Review, matching the Notebook launcher: machine
  profile, environment, Link to (Experiment / Project toggle + dropdown),
  input files, then GitHub repos. Machine selection uses 7 curated
  profile cards (5 CPU tiers plus GPU and high-memory defaults), with
  an Advanced expander that still surfaces the full GCE catalog.
- Surface the input file picker inline as soon as an experiment is
  selected, in both the Notebook and Work Node launchers. The old
  "Select files (N available)" link sat below the modal viewport and
  was easy to miss.
- Replace the legacy "Include FASTQ and BAM files" checkbox with a
  filter chip bar on the file picker: Defaults (10x cellranger trio
  plus h5ad), H5, CSV/TSV, Reports, FASTQ, BAM, Other. On open, only
  Defaults is active and matching files are pre-checked, so the common
  scRNA path needs zero clicks.
- Close the Work Node launch dialog immediately on Launch click and
  show a provisioning banner on the Work Nodes page. Errors surface
  in the banner with a Dismiss control. Removes the "is this hanging?"
  pause where the only feedback was a subtle "Launching..." label
  inside the dialog.
- Prompt before launching a Notebook or Work Node without any input
  files. Work Node additionally warns when GitHub repos are configured
  but none are linked to the run.
