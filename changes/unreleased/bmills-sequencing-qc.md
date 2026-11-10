### Pipeline launch

- The launch review now refuses an experiment, project, or run to resume from
  that does not exist or belongs to another organization, instead of
  previewing a sheet for it. The page explains that something the launch
  refers to could not be found, keeps Launch disabled, and offers Check again.
  Launching with such a reference is refused with the same message instead of
  failing with a server error.
- Opening a launch for an experiment that cannot be found no longer also shows
  a "Samples could not be loaded" outage message; the not-found explanation
  covers it. Server and network failures still show the outage message.
- A sample selection that includes samples from outside the experiment is now
  blocked at review, as it already was at launch, rather than previewing the
  remaining samples.
- The review now shows the pipeline's catalog defaults (for example a default
  strandedness) in the samplesheet, matching what the launch submits.
- A file linked to a sample can only reach a samplesheet, the review, or the
  run's recorded inputs if it belongs to your organization.
- The samplesheet shown at review, the one submitted to the pipeline, the
  run's recorded copy, and the sample names QC matches against now all come
  from one build of the sheet, so they cannot disagree.

### Experiments

- Creating an experiment with a project, template, or naming profile that does
  not exist or belongs to another organization is now refused with "not
  found", instead of linking it (and showing its name) or failing with a
  server error.

### Provenance and QC

- Project provenance (the graph and the report) lists only your
  organization's experiments, samples, runs, references, and snapshots, even
  where an older record links another organization's data to the project.
- A run report no longer shows a run from another organization as the run it
  resumed from.
- QC dashboards no longer show another organization's project name when a
  run's stored project belongs to someone else; the dashboard itself still
  opens.
