### Literature validation

- **A paper is no longer limited to the handful of assays bioAF had been taught by
  name.** Any nf-core pipeline this instance has installed can now validate a
  paper, and a paper that matches none of them is matched against the nf-core
  registry instead of being refused. A lab that installed the right pipeline is
  no longer told its paper is unreproducible.

  Assays bioAF now identifies directly: **CUT&RUN and CUT&Tag**, **small RNA and
  miRNA**, and **amplicon and 16S microbiome**, alongside the bulk RNA-seq,
  single-cell RNA-seq, ChIP-seq and ATAC-seq it already handled. Papers matched
  through the registry rather than by name stop at the data-quality tier, because
  bioAF has not verified what those pipelines publish. The plan says so before
  you approve it.

- **If the plan names a pipeline this bioAF does not have, the plan says so and
  offers to install it.** Until now, approval was accepted, the paper's whole
  dataset was downloaded, and only then did the launch refuse. This matters more
  now that a paper can map to any pipeline in the registry rather than to one of
  six: the common case is a real pipeline the lab simply has not installed yet.

- **bioAF now asks the data repository what the deposited data actually is, and
  believes it over the paper's prose where the two disagree.** A methods section
  saying "RRBS and RNA-seq" names two assays and bioAF used to take whichever it
  read first, so a methylation study could be planned as an RNA-seq run at full
  confidence. The deposited record is a controlled field, not prose, and it now
  decides. A compound methods sentence is also read as the several assays it
  names, rather than only the first.

  This applies to a study pinned to a specific dataset accession. Studies started
  from a paper in the Library are not pinned to one, so they still route on the
  paper's own words.

- **A plan whose pipeline cannot read the deposited data is refused before the
  money is spent, and now has two ways out.** Previously a scientist met the
  refusal only by pressing Approve and reading an error, with no control anywhere
  that resolved it and Decline, which is permanent, as the only remaining action.
  The gate now states the problem in its own panel above the approval controls
  and offers either **Use the pipeline the record names**, which corrects the plan
  in one click, or **Run it anyway**, which asks why and keeps the answer with the
  study so a result that later disagrees with the paper can be read against it.

- **A study pinned to a dataset reproduces that dataset only.** Papers cite other
  people's accessions alongside their own, and approving used to fetch every one
  the paper named. bioAF now fetches the one the study was requested for and says
  which others it left alone.

- **A study that stops on a technical failure now tells the person who asked for
  it**, in the app, with a link to the study and the Retry control. Nothing said
  anything before, so a stopped study was found by chance or not at all.

- **The data a stopped study downloaded is kept for three days and then freed.**
  Retry within that window and the download is reused, exactly as before. After
  it, the study is still retryable and downloads again. The study page says which
  side of that line it is on. Until now the download was billed indefinitely: one
  parked study held 114 GB for a week.

- **Approving after a retry says when it means downloading the data a second
  time**, in both the panel and the confirmation, instead of looking like a first
  approval.

- **Studies can now be run against organisms other than human and mouse**:
  zebrafish, rat, *C. elegans*, *Drosophila* and *Arabidopsis*. A run is also
  aligned against the genome the study identified rather than whichever one the
  pipeline was seeded with, and a paper naming more than one build has the choice
  stated on the plan instead of made silently.

- **Comparisons that need a per-sample design column can now launch.** The arms a
  scientist ratifies answer the columns the pipeline asks for, so routes that
  refused to launch for want of a group, replicate or patient column now run.

- **Single-cell studies read the authors' own deposited result tables**, including
  the Seurat format most single-cell papers publish, and the reproduced gene list
  is keyed by the same gene naming the paper deposited rather than a fixed one. 10x
  reads are parsed with the chemistry the samples declare instead of an assumed
  one.

- **ChIP-seq and ATAC-seq reproduce differential binding and accessibility.** Each
  sample is compared against its own control, and a deposited DiffBind result table
  is read as the ground truth it is.

- **A study that failed on infrastructure can be retried** from the furthest point
  its surviving work allows. A study whose data was already downloaded goes
  straight back to the analysis; one with nothing downloaded returns to the
  approval gate, so a re-download is a decision rather than a side effect. The
  state has been documented as retryable since it was written while nothing
  offered a way to do it.

### Pipelines

- **The disk on pipeline nodes is an operator setting** under Infrastructure >
  Components, and each step now declares the disk it needs so the scheduler stops
  packing nodes blind. Genome-scale alignments were being evicted part-way through
  after hours of work, twice on the same node, because the pool's disk was fixed
  in the installer and nothing asked for room.

- **A node pool that could not build a single node is refused before it is
  applied**, naming the quota that stops it and how many nodes the region will
  actually build. Faster disk types draw on a separate and typically much smaller
  quota, so a pool that looked reasonable could silently produce zero nodes and
  every run against it hung.

- **A run whose work can never be scheduled now fails and says why**, after ten
  minutes rather than never. A run with no schedulable work was indistinguishable
  from a healthy one.

- When a task is killed, bioAF records the reason while the evidence still exists,
  so an out-of-memory kill is no longer reported as an unknown failure with empty
  logs.

- Pipeline nodes no longer take public IP addresses, which removes an address
  ceiling that capped how many could run at once, and cancelling a run now
  cancels the work rather than leaving it running.

### Fixes

- A paper whose own wording was unusually long could make a study impossible to
  plan at all. The extraction failed and rolled back, and the study could not
  proceed.
- A reference genome bundled with the pipeline could override the one the study
  named, so a run aligned against a genome nobody chose.
- A finished peak-calling run could be discarded by a summary file sitting beside
  its real output.
- A study's result table is matched to the paper's columns with punctuation
  ignored, so a header written differently no longer reads as a missing column.
- Retrying a run asks for the same disk as the first attempt rather than an
  escalating amount, which had grown large enough to be unschedulable anywhere,
  and an abandoned run's intermediates are deleted after two days instead of
  being billed forever.
