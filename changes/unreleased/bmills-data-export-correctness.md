### Data exports

- Experiment and project exports now contain the actual contents of every selected file. Previously
  each data file in the archive was an empty placeholder.
- Archives use a new, collision-free layout. An experiment export's root folder is
  `experiment_<uuid>_<name>/`, with each data file at `results/<file-uuid>/<filename>` (FASTQ files
  under `raw/`). A project export's root is `project_<uuid>_<name>/`, with each experiment under
  `experiments/` and files that belong to the project but to no experiment at the project root. Two
  files with the same name, or two experiments whose names look alike, no longer overwrite each other.
- Exports and their size estimates now include files linked to an experiment through its samples and,
  for projects, files attached to the project itself. Each file appears once. Deleted files are left
  out. A project estimate's per-experiment rows follow the same assignment, so its total can exceed
  the sum of the rows.
- An export whose files include one with missing or deleted stored contents now fails with a message
  naming the file, instead of producing an incomplete archive. The same applies when a requested
  provenance report cannot be generated: the export fails rather than leaving the report out.
- Exports run one at a time per installation. While one is in progress, another request is refused
  with "Another data export is in progress"; try again in a few minutes.
- Exports have limits: 2 GiB of source files, 10,000 files, and 10,000 experiments per project.
  A requested provenance report is also limited by how much history and metadata it covers. Over
  any limit, the export is refused with a message naming the limit and suggesting a smaller export
  (leaving out FASTQ files or the provenance report, or exporting one experiment at a time).
- An export is refused when the server's disk would drop below a safety margin (the larger of 3 GiB
  or 10% of the disk). Exports are built on disk rather than in memory and streamed to the browser.
  A download that stops receiving data for 60 seconds, or runs longer than 30 minutes, is closed.
- Downloaded archives are named after the experiment or project (`<name>_export_<timestamp>.zip`),
  including names with non-English characters.

### Download links

- "Get Download Link" now reports how long the link actually stays valid ("expires in 23.99 hours"
  rather than always "24 hours"). On AWS, a link is never issued for less than one hour: if the
  server's credentials are about to rotate, the request is refused and you can try again later or
  download directly.
- Each download link points to its own uniquely named object. Exported files behind links are
  removed 48 hours after they are created on every storage backend (previously only on Google Cloud,
  after 24 hours, which could be before a link expired).
- Signed links are not available on NFS storage; choose "Download Now" there.

### Operators

- Two new settings, both optional: `BIOAF_EXPORT_TRANSFER_STALL_SECONDS` (default 60) and
  `BIOAF_EXPORT_SIGNED_MIN_TTL_SECONDS` (default 3600). Export scratch space lives under
  `/tmp/bioaf-exports` in the backend container.
- The storage Terraform for GCP and AWS adds a lifecycle rule to the config-backups bucket that
  removes old versions of exported files (under `exports/`) one day after they are replaced or
  deleted, and on AWS clears the delete markers left behind. Nothing else in the bucket is affected.
  The rule takes effect the next time the storage Terraform is applied. Google Cloud's soft-delete
  retention still applies after that.
- nginx's client send timeout is now set explicitly to 60 seconds on API routes (it was already the
  default).
