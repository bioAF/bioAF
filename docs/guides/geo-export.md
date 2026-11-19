# GEO Export

bioAF generates submission metadata packages for an experiment or a project's SuperSeries. These packages
contain spreadsheets, checksum records, validation output and instructions. They do not contain the actual
FASTQ or processed-data bytes, and bioAF does not upload them to GEO by FTP.
Use [Data Exports](data-exports.md) or your configured storage tools to retrieve the data files separately.

## Prepare the Records

Complete the experiment and Sample metadata, protocols, file associations and checksums. Review the
relevant completed Pipeline Run and QC before selecting it for export.

The experiment page has an **Export to GEO** button for the current `admin`/`comp_bio` UI roles.
Open the export, select a completed Pipeline Run, and choose whether to **Exclude failed samples**.
Use **Check Readiness** to review the metadata, then **Download Export** to retrieve the package.
Readiness and the download always select the same Samples and files. Changing the Pipeline Run or the
checkbox clears the displayed report; check readiness again.

### Which Samples are included

| Checkbox | `qc_status_filter` | Samples included |
| --- | --- | --- |
| Checked (default) | `exclude_failed` (also the default when omitted) | QC `pass`, `warning`, and not yet set; `fail` is left out |
| Unchecked | `include_all` | Every Sample in the experiment, including `fail` |

Warning and unset QC are not failed QC. Any other filter value, including `pass`, is refused (422) before
anything is gathered. If every Sample failed QC, exclusion selects none; it never falls back to all.

### Which files are referenced

A file belongs to a Sample only through its Sample link (made at upload, or with **Link** on the file).
Filenames are never used to decide ownership. Among the experiment's files:

- Linked to an included Sample: listed once in the package, and on each included Sample row it is linked to.
- Linked only to excluded Samples, or only to another experiment's Samples: left out.
- Not linked to any Sample (for example a count matrix): kept as an experiment-level file, on no Sample row.

Shared and experiment-level files are listed whole; bioAF does not read their contents, so a shared count
matrix may still contain measurements from failed Samples. The package lists files and checksums only.

### Files to review before download

Before Sample links decided ownership, a file whose name started with a Sample's ID was listed on that
Sample's row. If such a file has no Sample link and its name matches an included Sample, readiness lists it
under **File Associations** and **Download Export** stays disabled. The server refuses the download too
(409, `geo_file_associations_unresolved`), even if readiness was never run. Resolve each file in one of
two ways, which need `files:edit`:

- **Link to *Sample***: the file belongs to that Sample. It is then listed on that Sample's row only.
- **Keep at experiment level**, with a reason: the file stays listed for the experiment, without references
  on those Samples. No link is created, and this does not certify the file's contents.

Readiness runs again after each choice. A kept file is matched again if its name or storage location, or a
listed Sample's ID, changes, and a newly matching Sample needs its own decision. A file matching only a
failed Sample does not block an export that excludes failed Samples, but does block `include_all`.

The internal endpoints below provide programmatic access and require `files:download` plus access to
the requested Organization's records. Project SuperSeries export is API-only. These endpoints are not
part of the public Integration API; use a browser-session JWT for the programmatic examples.

## Experiment Package

```text
POST /api/experiments/{experiment_id}/export/geo?validate_only=true
POST /api/experiments/{experiment_id}/export/geo
```

Optional query parameters are `pipeline_run_id` and `qc_status_filter`. Validation-only returns a JSON report;
the export returns a ZIP. If no run is selected, the service selects a completed run using its review/run
selection logic.

The validation report's `file_association_findings` lists each file to review with its matched Samples,
whether each pair is resolved, and a `fingerprint`; `download_blocked` says whether the export will be
refused. To keep a file at experiment level through the API (requires `files:edit`), quote the finding:

```text
POST /api/experiments/{experiment_id}/export/geo/file-associations/acknowledgments
{"file_id": 12, "sample_ids": [3, 4], "fingerprint": "<from the report>", "reason": "Shared lane file"}
```

A fingerprint that no longer matches the current file and Samples answers 409; check readiness again. To
link instead, use `POST /api/files/{file_id}/link` with `{"sample_id": ...}`.

The ZIP contains:

- `geo_metadata_<experiment>.xlsx`
- `md5_checksums.txt`
- `validation_report.json` and `validation_report.txt`
- `README.txt`

Review the spreadsheet and both required-field gaps and unvalidated values in the report. Generating a ZIP
does not mean every required field is complete or that GEO will accept the submission.

## Project SuperSeries

```text
POST /api/projects/{project_id}/export/geo?validate_only=true
POST /api/projects/{project_id}/export/geo
```

The project endpoint supports `qc_status_filter`, `exclude_unclaimed` (default true) and an optional
`pipeline_run_ids` request-body map. It packages per-experiment submission material with SuperSeries metadata
and a unified manifest (a per-experiment summary, not a list of data files). Each experiment package follows
the Sample and file rules above. If any included experiment has files to review, the whole download is
refused (409) with the affected experiment IDs; resolve them through each experiment's readiness check.

Validation-only reports cross-experiment consistency checks over every eligible Sample. It accepts either
filter value but does not apply it, so a failed Sample still counts toward those checks.

## Submit to GEO

Follow the generated README and GEO's current submission instructions. Retrieve and verify the named raw
and processed files separately, transfer them using the submission mechanism GEO supplies, and review the
spreadsheet before submitting.

GEO packaging has its own service and in-memory ZIP response. The bounded scratch, signed-link delivery
and 2 GiB source-file contract in [Data Exports](data-exports.md) describe the general data-export path,
not this metadata-package endpoint.
