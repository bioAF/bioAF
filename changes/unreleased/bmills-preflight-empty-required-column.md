### Pipeline launches

- Refuse a launch whose selection holds a sample with no input files at the
  preflight, where every other block already lives, rather than at the button
  press. The check itself is not new: the launch has always run it, and the
  preflight did not, so a sheet with an empty required column was reviewed and
  reported as fine.
- Offer to drop those samples from the block itself, and preview the sheet with
  them already gone. The sheet reviewed is then the sheet submitted.
