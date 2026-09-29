### Literature validation

- Make Validate assess the paper's evidence without spending compute.
  Reproducing the paper's results moves to its own control beside the button,
  with the same route chooser and cost warnings.
- Reorganize the report around six areas: data and sample metadata,
  experimental methods, computational methods, published code and environment,
  results and reproduction, and interpretation. Each area lists supported
  observations, demonstrated concerns and open questions, with citations that
  open the source passage.
- Move both scorecards behind a single Scoring detail disclosure, and state
  checks attempted, checks conclusive and findings conclusive as separate counts.
- Assess each analysis a paper reports, and each published script, on its own,
  so a problem in one no longer settles another.
- Attempt the authors' published code where the study's authorization covers
  execution: stage the original source, install its declared dependencies, and
  run its entry point in a sandbox with no network access for the analysis. The
  transcript, exit status and any output comparison are recorded against the
  source revision that ran. A missing prerequisite is named in the report.
- Review whether each stated conclusion is supported by the paper's own
  evidence, without waiting for a run. A reproduction bioAF could not perform is
  reported as bioAF's limitation, not as evidence against the paper.
- Reconcile findings that cite the same evidence before publishing them, so a
  positive and a negative about one paragraph can both stand.
- Report assessment progress separately from execution progress, and report
  what bioAF holds now separately from earlier failed retrieval attempts.
- Name an exhausted or misconfigured model account above the report, with the
  billing or quota action that resolves it.
- The feature remains behind the `lit_validation` beta flag. Attempting author
  code requires a runner that supports an isolated namespace; otherwise the
  attempt is reported as blocked.

### Fixes

- Stop awarding analysis-coverage credit for a script whose only entry point
  prints a greeting, and environment credit for a manifest that only names a
  runtime version.
- Stop answering a computational preprocessing question from the cell culture
  protocol, and stop dropping relevant sentences that match no keyword list.
- Stop reporting an omission when the sources it is about were never retrieved
  or read.
- Stop reporting a species disagreement from a cell-line descriptor, or a
  sample-count disagreement between a whole series and one experiment's subset.
  Both now require a cited mapping between the experiment and the deposited
  samples.
- Read R Markdown as its code chunks instead of as unreadable or as prose.
- Stop treating a successful package import as evidence that the authors'
  analysis runs.
- Apply the runtime, resource profile, timeout and network policy to the job,
  and record what the job actually received.
- Stop returning a previous scientific conclusion when the model could not be
  reached, and stop reusing an interpretation whose evidence changed.
