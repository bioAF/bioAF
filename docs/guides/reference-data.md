# Reference Data Management

Reference Datasets record versioned reference inputs such as genomes, annotations, indices, atlases and
marker collections. Open **Data & Files > Reference Data** to browse and filter the catalog.

## Upload or Import

Use the add-reference workflow to choose local files or a source URL. Supply a name, category, explicit
version, scope (`internal` or `public`) and the source/description fields offered by that workflow. Scope is
reference governance metadata; it does not remove the installation's Organization boundary.

Local-file upload uses resumable upload sessions on the GCP reference path. Complete/finalize the upload
before treating the reference as available. If a transfer is abandoned, use the abort/cancel controls where
shown. URL imports run a background import job and show progress, bytes and errors; supported extraction
choices are `none`, `gzip`, `tar` and `tar.gz`.

An interrupted URL import can leave a reference awaiting finalization. Inspect import status and use
**Finalize** only when the files are ready. Recovery/finalization is not a request to restart the transfer.
These upload/import paths remain GCP-shaped; the existence of general S3 storage does not imply AWS parity
for reference ingestion.

## Versions and Use

Versions are explicitly supplied, not assigned as an automatic global v1/v2 counter. The **Versions** tab
groups records by name and category within the Organization and shows other versions newest first.
Existing runs retain their reference associations; creating a version does not rerun previous work.

Select the intended version through the pipeline's reference/input configuration and verify the run's
recorded reference associations. Do not assume a universal `/ref/` mount, automatic decompression or a
"track latest" policy for every workload; staging follows the selected provider and workload contract.

## Deprecation and Impact

A deprecation records a reason and optionally a superseding reference:

- An `internal` reference is deprecated immediately.
- A `public` reference enters `pending_approval`; approval changes it to `deprecated`.
- The **Impact** view lists associated Pipeline Runs and experiments so you can assess follow-up work.

The current API does not provide the old guide's warn/block policy selector, biological FASTA/GTF diff,
reactivate endpoint or automatic costed reprocessing wizard.

## Permissions

| Action | Required grant |
| --- | --- |
| List/detail/version/impact reads | `pipelines:view` |
| Upload, URL import, cancel, abort and recovery finalization | `references:upload` |
| Import-status polling | `references:view` |
| Metadata registration and deprecation | `pipelines:create` |
| Approval of public-scope deprecation | `infrastructure:configure` |

The navigation entry also requires file-view access. Controls use their backend grants; the built-in role
name alone does not establish access. Reference API routes are internal under `/api/references`, not
`/api/v1/references`.
