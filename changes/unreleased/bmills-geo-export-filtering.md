### GEO export

- Fix **Exclude failed samples** in Export to GEO, which worked backwards:
  checking it included failed samples and unchecking it left them out. Checked
  now leaves out samples whose QC is `fail` and keeps `pass`, `warning` and
  not-yet-set QC; unchecked includes every sample. Check Readiness and Download
  Export always select the same samples. If you downloaded a GEO export
  before this release, check which samples it contains before submitting it.
- The GEO export API accepts only `qc_status_filter=exclude_failed` (the
  default) or `include_all`. Any other value, including `pass`, is now refused
  instead of being treated as "include everything".
- A sample's files in the GEO spreadsheet now come from the file's sample link,
  not from the filename. Files linked only to left-out samples are no longer
  listed. Files linked to no sample, such as a count matrix, stay listed for
  the experiment but on no sample row. Each file is listed once, even when
  several samples share it.
- Files without a sample link whose names match a sample (how older uploads
  were matched) are listed under **File Associations** in Check Readiness.
  Download waits until you either link each file to its sample or keep it at
  experiment level, with a reason. Both need permission to edit files. A
  project (SuperSeries) export waits on the same decisions in its experiments,
  rather than leaving those experiments out of the archive.

### Updating

- Shortly after an updated bioAF starts, it reports which experiments have
  files waiting for that decision. The counts are in
  `update-status/schema-upgrade.json` and the file details in a private report
  in `backups/`. The report runs in the background: it never stops an update
  or delays bioAF starting, and only the affected GEO downloads wait. Resolve
  them from each experiment's Export to GEO, Check Readiness.
