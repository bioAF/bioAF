# GEO Export

bioAF generates submission metadata packages for an experiment or a project's SuperSeries. These packages
contain spreadsheets, checksum records, validation output and instructions. They do not contain the actual
FASTQ or processed-data bytes, and bioAF does not upload them to GEO by FTP.
Use [Data Exports](data-exports.md) or your configured storage tools to retrieve the data files separately.

## Prepare the Records

Complete the experiment and Sample metadata, protocols, file associations and checksums. Review the
relevant completed Pipeline Run and QC before selecting it for export. Check which Samples should be
included: the internal API's default `qc_status_filter=exclude_failed` excludes Samples marked `fail`.

The experiment page has an **Export to GEO** button for the current `admin`/`comp_bio` UI roles.
Open the export, select a completed Pipeline Run, and choose whether to **Exclude failed samples**.
Use **Check Readiness** to review the metadata, then **Download Export** to retrieve the package.

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
and a unified manifest. Validation-only reports cross-experiment consistency checks.

## Submit to GEO

Follow the generated README and GEO's current submission instructions. Retrieve and verify the named raw
and processed files separately, transfer them using the submission mechanism GEO supplies, and review the
spreadsheet before submitting.

GEO packaging has its own service and in-memory ZIP response. The bounded scratch, signed-link delivery
and 2 GiB source-file contract in [Data Exports](data-exports.md) describe the general data-export path,
not this metadata-package endpoint.
