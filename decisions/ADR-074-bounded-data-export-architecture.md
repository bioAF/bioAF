# ADR-074: Bounded data-export architecture

**Status:** Proposed
**Date:** 2026-11-14
**Deciders:** Brent (repository owner)
**Supersedes:** [ADR-036](ADR-036-data-export-download.md)

## Context

[ADR-036](ADR-036-data-export-download.md) introduced Experiment and Project data exports: a ZIP of the
selected files, README, sample manifest and optional provenance report, delivered either directly or
through a signed download link, with size estimates, the `files:download` permission and audit logging.
It set a 24-hour signed-link lifetime and an hourly cleanup of export objects older than 24 hours from
creation, so an object could be deleted while a link issued for it was still advertised as valid. It did
not define who owns the temporary archive, how much memory, disk and time one export may use, how many
exports may run at once, or what happens when a client disconnects or stalls. It also tied delivery and
cleanup to GCS, while bioAF now reaches storage only
through the BioAF Adapter Layer (BAL) on GCS, S3, local filesystem and NFS backends.

Without those rules an export can hold a whole archive in memory, fill the disk it shares with Docker
and PostgreSQL, keep running after its client is gone, collide or escape in archive names, report
success for content it could not read, and leave a link advertising a lifetime its object or signing
credentials cannot honor.

## Decision

Every experiment and Project export runs through **one shared path** in `backend/app/services/data_export/`
(`selection.py`, `naming.py`, `resources.py`, `archive.py`, `retention.py`), orchestrated by
`export_service.py`, `export_delivery.py` and `api/data_export.py`. The order of work is the contract:
scope and select, refuse what cannot succeed, admit, generate and build, hand over.

**Selection.** One scoped selection serves file listings, estimates and both builders. Every lookup is
scoped to the requesting Organization. An experiment includes its directly attached files and files
linked through its Samples; a Project adds Project-attached files and the files of its member
experiments. Deleted and unrelated files are excluded, FASTQ exclusion applies to both the file set and
the estimate, and each file is included once (its own in-Project experiment, otherwise the lowest
associated in-Project experiment, otherwise the Project root). A selected file whose stored bytes are
missing or unreadable fails the export (409) and is named; nothing is replaced by an empty placeholder.
Estimates remain metadata sums, not a storage snapshot.

**Archive layout.** One writer builds one ZIP containing real bytes; there is no intermediate
per-experiment ZIP merge. Roots are `experiment_<uuid>_<name>/` and `project_<uuid>_<name>/`, with
experiment roots under the Project's `experiments/`, and data at `{results,raw}/<file-uuid>/<name>`.
Every component is sanitized against separators, traversal, absolute prefixes, empty or dot-only names
and collisions, and bounded to 128 UTF-8 bytes per component and 512 per member. Download filenames carry
an ASCII `filename` fallback plus a UTF-8 `filename*`. The data export does not embed GEO metadata;
GEO submission remains its own service ([ADR-014](ADR-014-geo-export-service.md)).

**Provenance.** A requested provenance report is required: if it cannot be gathered or rendered, the
export fails. Gathering is budgeted per collection before reading (rows and row width measured in the
database), the gathered report's size is checked before rendering, and rendering runs in the export's
owned worker, off the event loop.

**Bounded resources.** Shipped defaults in `resources.py`:

| Bound | Default |
| --- | --- |
| Selected source bytes / files / ZIP entries | 2 GiB / 10,000 / 20,000 (413) |
| Generated content / final ZIP | 64 MiB / 2304 MiB |
| Scratch reservation | `R = S + max(L, G) + G + H`, `H = max(32 MiB, ceil((S + G) / 20))`; any unknown size reserves the 5 GiB ceiling; `R` above the ceiling is refused, never clamped |
| Free-space floor | `free - R >= max(3 GiB, 10% of the filesystem)` at admission, re-checked as writes grow (503) |
| Deadlines | 30 min preparation (selection through build), 15 min signed-link issuance, 30 min direct transfer (504 before delivery) |
| Transfer stall | 60 s without downstream progress (`BIOAF_EXPORT_TRANSFER_STALL_SECONDS`) |

Here `S` is the known selected source total, `L` the largest selected file and `G` the generated
allowance. Metadata is not trusted capacity: every scratch write goes through a bounded file that charges
positive high-water growth to the export's budget before the bytes reach disk, so a retried or
out-of-order range is charged once and an understated size fails rather than eroding the floor. Sources
are staged one at a time through the storage adapter's file API, copied with bounded buffers and deleted
before the next. The archive is never held in memory.

**Ownership of temporary artifacts.** Scratch lives in a private, server-named directory under
`BIOAF_EXPORT_SCRATCH_DIR` (default `/tmp/bioaf-exports`, mode 0700). From admission, one export job owns
its scratch directory and its slot lease; ownership passes to the direct response or the signed-link
upload and ends with exactly one idempotent release. Only request-owned files and objects are deleted.

**Controlled concurrency.** One active export per serving process, across users, Organizations and
delivery modes. The image runs one serving process, so this is the installation's only slot. A busy
request is refused (429) rather than queued; there is no per-tenant fairness. The slot is held through
build, transfer or upload, and worker settlement.

**Cancellation.** Disconnect, deadline, failure or shutdown cancels the export's budget, so the worker
stops at its next write; the build task itself is never cancelled mid-write. The waiting side allows 10 s
for settlement; a worker still running after that keeps its files and slot and releases them itself.
Scratch is never unlinked under a running worker. A direct response that never starts is released by a
backstop after the transfer deadline.

**Direct delivery.** Streamed from disk with `Content-Length` and `X-Accel-Buffering: no`. A stall guard
at the ASGI send boundary closes the stream when no nonempty body send completes for the stall limit, and
the absolute transfer deadline applies independently. nginx pins `send_timeout 60s` for the client leg;
its 3600 s `proxy_read_timeout`/`proxy_send_timeout` cover the backend leg. A failure after headers closes
the stream rather than returning a JSON error.

**Signed-link lifecycle.** Signed delivery requires the storage adapter's signing capability; stores
without it (NFS) are refused before any archive work, and the UI keeps direct delivery available.
Signing eligibility is also checked early. The archive is uploaded through the adapter to a unique key,
`exports/<org-id>/<uuid>.zip` in the config-backups store, and signed by the provider through
`sign_download`, which returns the link and its truthful expiry. Links request at most 24 hours and must
remain usable for at least `BIOAF_EXPORT_SIGNED_MIN_TTL_SECONDS` (default 3600) plus a 60 s handoff
allowance; the S3 adapter also keeps a 60 s credential safety margin and refreshes once before refusing.
A link that cannot meet the minimum is refused, never issued short. The response reports the remaining
lifetime, rounded down. Any failure before handoff (upload, signing, audit commit, final usability
check) triggers a shielded attempt to delete that request's object. Deletion is best effort: a failed
attempt is logged, and the object is left for retention to remove.

**Retention.** The hourly `export-cleanup` job lists the `exports/` prefix through the storage adapter
and deletes current objects 48 hours after their adapter-neutral upload time, which exceeds the issuance
budget plus the maximum link lifetime. Objects from the earlier timestamp-named scheme are deleted only
after both 24 hours from `platform_config.export_retention_activated_at` (inserted once, never
overwritten) and 48 hours from upload. Objects with an unknown upload time are left alone. A late or
failed pass only extends retention. Storage Terraform for GCP and AWS expires noncurrent `exports/`
versions after one day; deleting the live object ends access, not necessarily physical erasure.

**Audit.** Each export records `data_exported` with its options and a stage (`archive_prepared` or
`link_issued`, with archive size and link expiry). The record states which stage the export reached; it
is not evidence of successful delivery. The audit commits before handover, so a later failure (a signed
link that no longer meets the minimum lifetime, or a direct transfer that stalls or disconnects) can
follow an audit record. The record never claims client receipt.

**Carried over from ADR-036:** the experiment and Project endpoints (`/export/estimate`, `/export/data`),
FASTQ (default off) and provenance (default on) options, metadata-based estimates, direct and
signed-link delivery (wire value `gcs` retained for provider-neutral signed delivery), the large-export
recommendation of signed links where supported, the `files:download` permission, immutable audit
logging ([ADR-009](ADR-009-immutable-audit-log.md)), the 24-hour maximum link lifetime, and single and
multi-file downloads from the Files page through per-file signed links.

**Replaced:** the unbounded in-memory archive; GCS-only upload, location and cleanup (now any BAL store
with provider-issued links); timestamp-named export objects; the 24-hour creation-age cleanup (now 48
hours from upload); the 24-hour object lifetime that could end before its link; embedding GEO metadata in
data exports; the archive layout keyed by sample IDs and names; silent omission of failed provenance; and
audit details that implied delivery.

## Rationale

- **Server-side ZIP to scratch, kept.** Streaming per-file signed links for a whole Experiment or Project
  loses the README, manifests, provenance and a single coherent package; building in memory is unbounded.
  A disk-backed archive under a reservation keeps memory flat and makes disk use predictable.
- **Size-based reservation over a fixed threshold.** A fixed large reservation refuses useful small
  exports on ordinary boot disks; a per-export formula admits them while still refusing large or unknown
  selections. Unknown sizes reserve the ceiling instead of being read as zero.
- **One global slot, no queue.** At a concurrency of one, a queue or per-user map adds machinery without
  protecting the shared disk. More processes would not provide fairness either; multi-tenant admission is
  a separate decision, not inherited silently from this default.
- **Budget cancellation over task cancellation.** Threads cannot be interrupted; cancelling the budget
  lets the worker stop at a known point, and letting a late worker keep its files avoids deleting scratch
  under it.
- **Provider-issued links through the adapter.** Signing stays inside each storage adapter with its
  credential rules, so the export path needs no provider branches and never advertises a lifetime the
  provider will not honor.
- **Retention longer than the link.** Deleting an object before its advertised link expires breaks a
  valid download; 48 hours from upload covers the 24-hour maximum plus issuance with margin.

## Consequences

**Positive:** exports contain real, uniquely named bytes; memory, disk and time are bounded and refusals
name the limit; abandoned exports stop and their artifacts are released or left for retention; signed links are honest about their lifetime on every
signing backend; retention works on every store.

**Negative / honest limits:**

- Exports above 2 GiB of source data are refused; users export smaller scopes or omit FASTQ files.
- One export at a time per installation; a long export blocks others, across Organizations, until it ends.
- Scratch shares the host disk with Docker and PostgreSQL. The floor is an export refusal rule; it does not
  stop other writers consuming space between checks.
- The direct-download UI still buffers the archive in browser memory; server bounds do not bound the
  browser.
- Bucket versioning and provider soft delete can keep deleted export bytes past 48 hours.

**Neutral:** the shipped limits are a provisional envelope, to be revised from measured capacity on
representative installs. Changing them is a configuration or constant change, not a new design.

## References

- [ADR-036](ADR-036-data-export-download.md) (superseded), [ADR-009](ADR-009-immutable-audit-log.md),
  [ADR-012](ADR-012-data-portability.md), [ADR-014](ADR-014-geo-export-service.md),
  [ADR-032](ADR-032-custom-rbac.md), [ADR-037](ADR-037-provenance-reporting.md),
  [ADR-065](ADR-065-bal-normalized-contract.md)
- [Data Exports guide](../docs/guides/data-exports.md), [Configuration](../docs/configuration.md)
- `backend/app/services/data_export/`, `backend/app/services/export_service.py`,
  `backend/app/services/export_delivery.py`, `backend/app/api/data_export.py`,
  `backend/app/api/streaming_responses.py`, `docker/nginx.conf`
