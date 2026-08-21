### Pipeline launches

- Build every samplesheet from the pipeline's own `schema_input.json` instead of
  a fixed `sample,fastq_1,fastq_2`, so each pipeline gets the columns it actually
  declares, in a stable order, with only the ones bioAF can fill.
- Say a run cannot start **before** Launch is pressed, naming the columns that
  are missing, the samples they are missing for, and what would fix each one.
  Previously a mismatched sheet failed inside Nextflow minutes later, after a
  node had scaled up.
- Show the sheet a launch would submit, as a table and as raw CSV, on every
  launch. Any cell can be corrected in place for that run.
- Ask for the values bioAF cannot derive, one row per sample, with the
  pipeline's own allowed values and error text as the hint.
- Launch from any per-sample file a pipeline declares, not only sequencing
  reads, which unblocks pipelines whose input is a BAM, CRAM or image.
- Name the exact characters a pipeline's schema rejects and recommend a spelling
  it would accept, rather than reporting a present value as missing. Nothing is
  ever renamed on the scientist's behalf.
- Refuse to emit a sheet whose rows the pipeline could not tell apart, and fill
  the distinguishing column where bioAF holds the fact: `lane` from the file's
  own lane, and `run` from its archive run accession or flow cell.
- Where no value could resolve a repetition, say so and name the remedy instead
  of offering a field that cannot help. Two lanes of one flow cell are one
  sequencing run, and ampliseq takes one row per sample whatever is typed.
- Declare the samplesheet columns for a pipeline that publishes no contract, and
  bind each to a sample field, a read, a file type, a custom field or a fixed
  value. The declaration binds the run in front of you, and saving it for next
  time stays a separate, deliberate step.
- Save a samplesheet design and have it offered back on the next run, at
  experiment, project or organisation scope.

### Provenance

- Keep the samplesheet each run was given, exactly as submitted, together with
  who stated each value in it. It is a record of what ran, never a sheet rebuilt
  from today's data.
- Show that sheet with each row's asset identifier beside it, behind a toggle,
  so a misattribution can be checked by eye. The identifier is never submitted
  to the pipeline.
- Attribute a pipeline output to its sample by identity, falling back to the
  name the run actually emitted rather than the name the sample carries now. An
  output that names no sample attaches to the run instead of to every sample in
  it.

### Samples and files

- Record a file's sequencing identity as typed columns (flow cell, lane, read
  type, index sequence, archive run accession), read from the FASTQ header on
  ingest rather than guessed from the filename.
- Name the unit between a sample and its files a **Read Group**, the term the
  SAM specification and every aligner already use, and expose it per sample.
- Give every file a catalogue identity that survives a move or a rename.
- Retire a deleted file from view without removing it from the catalogue, so an
  exported dataset or a published provenance record never dangles.

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

### Fixes

- Keep the part of a failed run's log that says what failed. The stored message
  was capped in a way that discarded the process name and exit status.
- Stop the dashboard being scrolled by content inside a hidden table, and keep
  absolutely positioned elements inside the page scroller.
- Render the parameter form for schemas that use `$defs`, and read vocabularies
  a schema declares numerically or in branches.
- Re-fetch a pipeline's samplesheet contract when its version moves.
- Say when a registry pipeline has no release available to install.
- Offer the 10x protocol only to pipelines whose own schema accepts it.
- Refuse a lab-document import URL whose host cannot be shown to resolve to a
  public address, instead of attempting the fetch.
