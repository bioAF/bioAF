### Pipeline launch

- Pipeline parameters now reach Nextflow exactly as entered. A value containing
  shell characters, quotes, a leading dash or text such as `--outdir` is passed
  as that parameter's value instead of being run as a command or read as
  another option.
- Parameter names must start with a letter or underscore and contain only
  letters, digits, `_`, `.` or `-`; a launch with any other name is refused at
  review and at launch with an explanation.
- `outdir` and `input` are set by bioAF for every run and can no longer be
  supplied as parameters. Each run writes its outputs to its own results
  location.

### Pipeline outputs

- Completed runs collect their outputs from their own results location only.
  An older run that recorded a different output location is skipped with a
  warning instead of collecting data from elsewhere.
- Outputs of custom pipelines started from a project (not an experiment) are
  now collected from where the pipeline wrote them.
- Local and NFS installs now register pipeline outputs; previously they found
  none.

### Files

- Files whose names contain `?` or `#` can be downloaded, previewed and used as
  inputs on every storage backend. Previously the part of the name from that
  character on was dropped, so the file could not be found.

### Storage cleanup

- Cleanup of failed runs' working files deletes only that run's files; a run
  numbered 1 no longer shares a prefix with runs 10 or 100. Cleanup actually
  runs now (it had been failing silently).
- Cleanup of stopped validation studies checks every file it would delete
  first. If any file lies outside the fetch run's own output location, nothing
  is deleted and the study is flagged for review. An interrupted cleanup is
  finished on a later pass, and a retry in between returns to the approval
  step instead of reusing deleted data.
- Retrying a stopped validation study and cleaning up its downloads can no
  longer happen at the same time: a study retried while cleanup is starting
  keeps its data, and cleanup that has begun is always finished rather than
  undone, even when storage reports an error for a delete it may have carried
  out.

### Notebooks, custom pipelines and work nodes

- Files whose names contain spaces, quotes, `$`, backticks, brackets, `*`, `?`,
  `#`, newlines or other special characters are copied into notebooks, custom
  pipelines, cellxgene and work nodes as exactly that file. Previously such a
  name could run a command or copy a different file, and on GCP a name with
  `[`, `*`, `?` or `#` followed by digits stopped the run before it started.
  On GCP, such files are checked against the stored checksum before use, and a
  failed download leaves any existing file in place.
- Custom pipelines on AWS copy all of their outputs (previously the copy failed
  when there was more than one output file).
- Custom pipelines on GCP copy every output exactly as named, including names
  with brackets, `*`, `?`, names ending in `#` and digits, and hidden files
  (previously such a name could copy a different file, be skipped, or be
  stored under a shorter name). Google Cloud Storage cannot hold a name with
  a line break or carriage return: such outputs are listed in the run's log,
  everything else is copied, and the run fails.
- A custom pipeline whose outputs could not be copied to storage now fails
  instead of being reported complete; a pipeline that already failed keeps its
  own exit status.
- On NFS storage, a session whose storage root is not mounted now reports that
  instead of appearing to save.
