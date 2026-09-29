### Literature validation

- **Validate now assesses the paper instead of asking what to spend.** Clicking
  Validate on a paper reads it and assesses its evidence, which costs no compute.
  Reproducing the paper's results moved to its own control beside the button,
  with the same route chooser and the same cost warnings. The question used to
  come first, before bioAF had read the paper, which is before it could know
  which route was even possible.

- **The report answers the six questions a scientist actually has.** It opens
  with a short account of what bioAF established and expands into data and
  sample metadata, experimental methods, computational methods, published code
  and its environment, results and reproduction, and interpretation. Each area
  carries supported observations, demonstrated concerns and questions bioAF
  could not settle side by side, with citations that open the source passage.
  No area is reduced to a single pass or fail, because most papers are not one
  or the other.

- **One number, not two.** Two scorecards with different meanings used to sit at
  the top of the report, and nothing said which to believe. Both are kept in
  full behind a single Scoring detail disclosure. Three counts that used to
  disagree are now stated separately and add up: checks attempted, checks
  conclusive, findings conclusive.

- **Each analysis a paper reports is assessed on its own.** A paper with a bulk
  RNA-seq arm, a single-cell arm and a screen now gets a separate answer for
  each, and the points follow the same split, so a problem found in one arm no
  longer settles another. Each published script is assessed separately too:
  running one script establishes nothing about the rest.

- **The authors' published code is attempted, not only read.** Where the study's
  authorization already covers execution, bioAF stages the original source,
  installs the dependencies the paper declared, and runs the authors' own entry
  point in an isolated sandbox with the network closed to the analysis itself.
  The transcript, the exit status and any comparison with the paper's reported
  values are recorded against the exact source revision that ran. Where the
  inputs, the runtime or the authorization are missing, the report names that
  specific prerequisite rather than saying nothing at all.

- **Whether a result supports its conclusion is assessed without waiting for a
  run.** Each conclusion the paper states is reviewed against the design,
  uncertainty and population actually examined, from the paper's own evidence.
  A reproduction bioAF could not perform is reported as bioAF's limitation and
  never as evidence that an effect is absent. Running the authors' code,
  independently supporting a result and supporting the authors' interpretation
  stay three separate conclusions.

- **Findings that rest on the same evidence are reconciled by reading them.**
  A positive and a negative citing the same paragraph are examined together
  before publication, so "the processing is described" and "the described
  statistical design is inappropriate" can both stand. One demonstrated failure
  still costs one deduction, and a genuine conflict the evidence cannot settle
  stays visible instead of quietly withdrawing a finding.

- **How far the assessment has got is reported apart from what the run is
  doing.** A study whose data could not be acquired and a study whose evidence
  has been reviewed in full no longer look the same, and the report says which
  revision it is showing.

- What bioAF holds now is reported apart from the attempts that failed getting
  it. A supplementary file that arrived on a later attempt is no longer listed
  as a current failure, the earlier attempts stay readable as history, and a
  recommended action has to address the recorded cause, so nothing asks for
  credentials to fix a file that does not exist.

- An exhausted or misconfigured model account is now named as one, above the
  report, with the billing or quota action that would resolve it, instead of
  reading as a failure of the paper.

- The feature remains behind the `lit_validation` beta flag. Attempting the
  authors' code needs a runner that can supply an isolated namespace; where it
  cannot, the attempt is reported as blocked rather than as a defect in the
  paper. Unsupported languages, documents with no standalone script, missing
  compatible inputs and outputs that cannot be compared are reported as scoped
  limitations.

### Fixes

- Stop awarding analysis-coverage credit for a script whose only entry point
  prints a greeting, and environment-reconstruction credit for a manifest whose
  only content is a runtime version. Both are recorded as the observations they
  are and judged against the steps the paper actually claims.

- Stop answering a question about computational preprocessing from the cell
  culture protocol. Evidence is selected a section at a time, so a bench
  procedure no longer reaches a computational obligation one sentence at a time,
  and a relevant sentence is no longer dropped for using words no keyword list
  knew.

- Stop reporting that a paper omitted something when the omission rests on a
  search that did not finish. Coverage records what was not read, and a positive
  answer about a source bioAF never retrieved is held rather than asserted.

- Stop reporting a species disagreement from a cell-line descriptor, and a
  sample-count disagreement between a whole series and one experiment's subset.
  Both now require a cited mapping between the paper's experiment and the
  deposited samples, and stay undetermined without one.

- Read R Markdown as the code chunks it contains, with their own source
  locations, instead of treating the document as unreadable or parsing its prose
  as code.

- Stop crediting a successful package import as evidence that the authors'
  analysis runs. A dependency check now establishes dependency facts only.

- Stop recording an execution limit that nothing applied. The runtime, the
  resource profile, the timeout and the network policy reach the job, and what
  the job actually got is recorded beside what was asked for.

- Stop returning a previous scientific conclusion when the model could not be
  reached, and stop reusing an interpretation whose underlying evidence changed.
