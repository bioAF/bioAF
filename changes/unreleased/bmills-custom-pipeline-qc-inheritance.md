### Custom pipelines

- Fix pipeline versions created automatically by an environment rebuild losing
  their QC settings. They now keep the source version's QC template and QC
  config, so later runs show the same QC dashboard layout as before the
  rebuild, and reference variables keep their category, so the launch picker
  stays limited to it. Versions created by earlier releases are not changed:
  their runs use the default custom QC layout and offer every reference
  category. The New Version form pre-fills from such a version without these
  settings, so re-enter them there to restore them for future runs.
- Operators can list the affected versions, the runs that used them, and those
  runs' QC dashboards with the read-only
  `python -m app.cli.assess_cascade_qc` command. It changes nothing.
- A pipeline version can no longer use an environment from another
  organization: it is rejected like an environment that does not exist. An
  environment rebuild now creates new versions only for pipelines in its own
  organization.
