### Results and QC

- Count the samples a run covered, rather than the sequencing files it read. A
  pipeline that runs no aligner publishes nothing that says which files belong
  to one sample, so a single sample sequenced over two lanes was reported as
  four samples, and its "reads per sample" was a per-file mean: half the real
  depth. bioAF now takes the roster from the samplesheet the run itself
  submitted.
- Where bioAF has no record of that sheet, report neither the sample count nor
  the read depth, and say so, rather than showing a file count under the word
  "Samples". Existing dashboards keep their values until they are regenerated.
- Say "1 sample" rather than "1 samples".
