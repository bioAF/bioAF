### Results and QC

- Surface AI Review on the QC report. A minimizable "AI Reviews" panel at the
  top of the report lists the run's review cards (click to open the full
  review), and a "Run AI Review" / "Re-run AI Review" button starts a review.
  This appears on the standalone QC dashboard, the Pipeline Run Results tab,
  and the Experiment Results tab.
- Anyone who can view experiments or pipelines can now read AI reviews on the
  QC report; running or dismissing a review still requires the AI Review
  permission.
- The Experiment detail Results tab now opens the same full QC report used
  elsewhere in a modal (click outside to close), instead of a reduced
  look-alike.

### Fixes

- Fix broken plot previews on the Experiment Results tab (PDF plots now show
  their thumbnail and open the plot viewer, matching the Plot Archive).
- Show full context on the Experiment Results tab QC cards (pipeline, project,
  experiment, and samples) instead of just the run number.
- Show the AI Review trigger to computational-biology users, not only admins.

### Permissions

- The Results menu and the Results tabs are hidden from users who cannot view
  experiments or pipelines.
