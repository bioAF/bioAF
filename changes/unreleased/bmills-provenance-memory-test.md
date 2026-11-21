### Fixes

- A project export with the provenance report now checks every report it
  includes (the project's and each experiment's) before generating any of
  them. Before, when the project's report, or a later experiment's, was too
  large to include, the export still generated the earlier experiments'
  reports first, using memory and time, and only then refused. It now refuses
  straight away with the same "too large" message.
