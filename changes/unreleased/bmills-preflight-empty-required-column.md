### Pipeline launches

- Say a run cannot start when its selection holds a sample with no input files,
  before Launch is pressed rather than after. The check itself is not new: the
  launch has always refused these, and the preflight did not run it, so a sheet
  carrying a row with an empty required column was reviewed and reported as fine.
- Name the samples that have nothing attached, and offer to drop them from the
  block itself. Previously that offer arrived only as a dialog after the launch
  had already been refused.
- Preview the sheet with the dropped samples already gone, so the sheet reviewed
  is the sheet submitted rather than one carrying rows the run will not contain.
- Ask again when the selection changes, so a sample added after the decision is
  never dropped by an answer given before it existed.
