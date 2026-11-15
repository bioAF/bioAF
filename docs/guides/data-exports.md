# Data Exports

Experiment and project exports package registered file contents and research metadata as a ZIP. They are
separate from [GEO submission metadata](geo-export.md). Start from the relevant experiment or project page
and open its data-export action. Exporting requires the backend's file-download permission and access to
the requested Organization's records.

## Choose Contents and Delivery

1. Choose whether to include FASTQ files and a provenance report.
2. Calculate the size estimate and review the project breakdown where available.
3. Choose **Download Now**, or **Get Download Link** if the storage backend supports signed downloads.
4. Keep the page open until the archive arrives or a link is returned. Retry explicitly after a failure.

The estimate uses selected-file metadata, so it can be smaller than the ZIP's actual space requirement.
Generated manifests, metadata and reports also need space. A project total can exceed the sum of its
per-experiment rows because project-only files are included as well.

## File Selection

- An experiment includes its directly attached files and files linked through its Samples.
- A project includes every experiment whose `project_id` matches the project. Its file set includes files
  attached directly to the project, files attached directly to those member experiments, and files linked
  through those experiments' Samples. There is no experiment-subset selection.
  Explicit `project_samples` membership alone does not include files from an experiment outside the project.
  Files qualifying through more than one association are assigned once.
- Deleted files are excluded. Excluding FASTQ files applies to the file set and the estimate.
- A selected file with missing/deleted stored contents fails the export and identifies the file; it is not
  silently skipped. A requested provenance report that cannot be generated also fails the export.

## Archive Layout

Names are sanitized for extraction and qualified by UUIDs to avoid collisions. A representative experiment
archive contains:

```text
experiment_<uuid>_<name>/
  README.txt
  sample_manifest.csv
  results/<file-uuid>/<filename>
  raw/<file-uuid>/<filename>        # FASTQ, when included
  ...                             # optional provenance files
```

A project uses `project_<uuid>_<name>/` as its root, with experiment folders under `experiments/`.
Project-only files are assigned at project scope. Metadata/report entries depend on the selected options.
Download filenames are `<name>_export_<timestamp>.zip` and support non-English names.

## Limits and Busy Responses

| Bound | Current value |
| --- | --- |
| Concurrent data exports | One per installation; requests are refused rather than queued |
| Selected source bytes | 2 GiB |
| Selected files | 10,000 |
| Experiments per project archive | 10,000, also subject to other archive limits |
| Direct transfer | 30 minutes total, with a 60-second idle timeout by default |
| Free disk floor | Larger of 3 GiB or 10%, after reserving scratch |

Requested provenance is separately bounded by input rows, metadata bytes and rendered-report input size.
The refusal message names the limit. Try fewer files, omit FASTQ or provenance, or export experiments
separately. A direct download uses server scratch and streams the archive to the browser; it does not
remove these preparation limits.

## Signed Download Links

A link points to a unique temporary export object and requests at most 24 hours of validity. The UI reports
its actual remaining lifetime, not a guaranteed 24 hours. AWS credential expiry can shorten it; if the link
cannot meet the minimum lifetime (one hour by default), the request is refused. Try later or download directly.
NFS does not support signed links, so use **Download Now**.

Successful temporary export objects are eligible for cleanup 48 hours after upload on each backend; the
hourly cleanup job performs deletion. GCP/S3 noncurrent-version cleanup rules take effect after the storage
Terraform is applied. Provider versioning or soft-delete retention can keep deleted bytes longer.
Treat a signed URL as a bearer credential and share it only with intended recipients.

## Operator Details

Scratch defaults to `/tmp/bioaf-exports` in the backend container. The backend owns the job's temporary
files through preparation, transfer and cleanup. nginx's API `send_timeout` is 60 seconds for sending
to the client. Its `proxy_read_timeout` and `proxy_send_timeout` are 3600 seconds for communication with
the backend.
See [Configuration](../configuration.md) for supported timeout and signed-link settings.
