### Notifications

- In-app notifications are now clickable and open the item they refer to: a
  completed run's QC dashboard, an experiment, an uploaded file, and so on.
  Clicking still marks the notification read. Existing notifications are
  backfilled so they link too.

### Navigation

- Surfaced Experiments as a top-level sidebar section (it was nested under
  "Projects"); the sub-menu is unchanged.
- Renamed the two identical "Environments" sidebar entries to "Pipeline
  Environments" and "Compute Environments".
- Reordered the sidebar to lead with the science: Dashboard, Experiments,
  Pipelines, Results, Workbench, Data & Files, Profile, Infrastructure, Settings.

### Global shell

- Added a global search box to the header to jump to any experiment, sample,
  pipeline run, or file by name (results appear after you pause typing).
- Added a "+ New" quick-create menu (New Project, New Experiment, New Sample).

### Results

- The QC dashboard detail is now labeled by Project / Experiment / Pipeline and
  links back to its pipeline run, instead of the unplaceable "QC Dashboard -
  Run #N".
