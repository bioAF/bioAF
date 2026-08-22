### Quality control

- Corrected the read depth shown for a sample whose Read Groups happened to
  yield the same number of reads. A quality report gives one entry per file and
  never says which files belong together, so bioAF told a paired mate from a
  separate Read Group by whether the two reported an identical count. Two Read
  Groups that landed on exactly the same count were therefore read as a single
  mate pair, and the sample's depth came out short by one Read Group's worth: a
  sample sequenced twice at 33.4M reads each showed 33.4M rather than 66.9M.

  bioAF no longer has to guess. The sample sheet it submits carries one row per
  Read Group, and every run keeps a copy of the sheet it sent, so a sample's
  files divided by the rows it was submitted over gives the answer outright.

  What changes when you regenerate a dashboard: a sample whose Read Groups
  produced identical read counts shows its full depth. Every other run is
  unaffected, including single-end runs, paired-end runs, and samples with one
  Read Group. A run launched before bioAF began keeping a copy of the submitted
  sheet is read exactly as it was. Existing dashboards keep their current
  numbers until regenerated.
