# ADR-073: Run-owned storage and pipeline parameters as data

**Status:** Proposed
**Date:** 2026-11-14
**Deciders:** Brent (repository owner)
**Related:** [ADR-020](ADR-020-bioaf-adapter-layer.md) (adapter layer; its 2026-11-10 amendment
retired adapter staging and made `collect_outputs` discovery-only),
[ADR-022](ADR-022-gcs-storage-backend.md) (GCS storage backend; same amendment),
[ADR-038](ADR-038-pipeline-io-lineage-junction.md) (pipeline IO lineage),
[ADR-044](ADR-044-custom-pipelines.md) (Custom Pipelines)

## Context

A Pipeline Run writes outputs to object storage (GCS, S3 or NFS through the storage adapters),
and several independent consumers act on that location afterwards: the compute adapter that
renders `--outdir`, the pipeline monitor and the registration CLI that collect and register
outputs, the validation driver that ingests fetched FASTQ, and the reapers that delete run data.
Each of these used to derive the location for itself, and each trusted a stored
`parameters_json.outdir` that originated in the launch request. A launch parameter could
therefore name another bucket or another run's directory, and whatever was found there would be
registered as this run's output (ADR-038 lineage) and later deleted as this run's data.

Two further forces apply. Object-store prefixes match literally, so `run-1` also matches `run-10`
and `run-100`. And every Pipeline Run reaches its container through generated shell text: the
Nextflow head pod's `/bin/sh -c` command, and the init containers, EXIT traps and build templates
that stage inputs and copy outputs. Parameter values, filenames and URIs are user data inside
that text.

## Decision

**1. One authorized output location per Pipeline Run, derived from the run's identity.**
`app/platform/run_output_layout.py::run_output_key` defines the key:
`experiments/<experiment id>/pipeline-runs/<run id>`, or `projects/<project id>/pipeline-runs/<run id>`
for a project-scoped Custom Pipeline run. The results store is `results_bucket_name`, else derived
from a `bioaf-raw-` raw bucket (`results_bucket_from_config`), else the storage adapter's own
`resolve_uri(RESULTS, ...)`; the adapter mints the URI, so the rule is provider-neutral.
This rule is the contract for every party that touches a run's outputs. Writers that place outputs
(the Nextflow renderer and the Custom Pipeline exit step) and readers that collect, register or delete
them must resolve the same location by the same rule, including the same results-store fallback.
`app/services/run_output_location_service.py::RunOutputLocationService.authorized_uri` is the
resolver for a loaded run's location when reading, registering or deleting. A stored `outdir`
is history, not authority: it is revalidated at every consumption, and any value other than the
run's own location raises `OutputLocationRefused` before any storage access. No legacy exception
widens this, and nothing is redirected.

**2. The platform owns `input` and `outdir`.** `app/platform/nextflow_params.py` declares
`PLATFORM_OWNED_PARAMS = ("input", "outdir")`. `PipelineRunService` calls
`refuse_unlaunchable_parameters` on the merged parameters (fresh launches and reproductions alike),
so a launch naming either is refused with a 400 before the run row exists. Because the run id is
part of the location and does not exist until the row does, a caller cannot name its own location
in advance, so any `outdir` is refused. The renderer emits `--input` as the fixed samplesheet path
and exactly one `--outdir` with the authorized location, last. `fusion_enabled` and `accessions`
are internal carriers (`INTERNAL_PARAMS`) and are never emitted.

**3. Parameters and operands are data, never shell syntax.**

- Nextflow: `KubernetesComputeProvider._build_nextflow_command` keeps the fixed
  `cat /data/nextflow.config &&` prologue and renders every token after it with `shlex.join`. Each
  parameter is one `--key=value` argument (`parameter_argument`), so a value can neither run a
  command nor become another flag, while Nextflow still types `true` and numbers as before. Names
  must match `[A-Za-z_][A-Za-z0-9_.-]*`; this is checked at launch and again in the renderer
  (`refuse_malformed_parameter_names`), because typed, legacy and replayed specs reach the
  renderer without passing the launch. A pipeline source or version starting with `-` is refused.
- Storage CLI commands: `app/platform/shell_args.py` is the one encoding. `shell_word` quotes
  every literal with `shlex.quote`; a caller-owned runtime expression (such as a loop's `"$f"`)
  must be typed `ShellExpr` to be inserted verbatim. There is no third kind. Local operands must be
  absolute (`absolute_path`) and remote operands must carry the adapter's own scheme, so no operand
  reads as an option; staged relative paths refuse absolute, empty, `.` and `..` segments
  (`safe_relative_path`). Each adapter keeps its own CLI programs, flags and provider-specific
  escaping (for example GCS pattern characters).
- Payload files: the Custom Pipeline `manifest.json` and `params.json` init steps and the notebook
  `FILE_INVENTORY.md` are written with `printf '%s'` of one single-quoted argument, not a heredoc,
  so no delimiter can collide with data.

**4. Collection and deletion stay inside authorized locations.**
`app/platform/run_output_layout.py::uri_within(storage, uri, prefix)` is the membership rule: both
URIs parse through the adapter, scheme and bucket must match, no key segment may be empty, `.` or
`..`, and the match is on a `/` boundary. `directory_prefix` terminates a directory with one
separator before listing.

- `collect_outputs(output_uri, pipeline_run)` on every storage adapter lists the separator-terminated
  authorized URI and returns canonical URIs; it copies nothing (ADR-020 amendment).
- `WorkDirReaper` lists `work_dir_key(run) + "/"` (`app/platform/work_dir_layout.py`) and refuses
  the run if any listed object falls outside it.
- `ValidationFetchReaper` authorizes everything before deleting anything: the fetch run must
  belong to the study's Organization and experiment, every registered File URI must be within the
  run's authorized location, and every work-dir object within its own prefix. Any failure flags
  the study (`fetch_reap_blocked`) and skips it whole. Deletion is ordered for what a failure leaves
  behind (unlink and commit, then delete objects, then rows and the completion marker), with
  `fetch_reap_pending` carrying the obligation; no storage/database atomicity is claimed.
- The local filesystem object store (`app/adapters/storage/local_fs.py::LocalObjectFilesystem`,
  shared by GCS local, S3 local and NFS) applies the same containment to listing roots and every
  enumerated entry as to direct operations.

**5. Built-in and Custom Pipelines.** The location rule (1) and containment (4) apply to both: the
monitor and registration CLI resolve every run through `authorized_uri`. The parameter rule (2)
and Nextflow rendering (3) are Nextflow-specific. Custom Pipeline variables are not Nextflow
arguments: they reach the job as `params.json` keys and `PARAM_<NAME>` environment variables, and
`authorized_uri` never reads a Custom Pipeline run's `parameters_json` as a location, so a
variable named `outdir` is plain data. The Custom Pipeline exit step must copy `/outputs` to the
run's authorized location as defined by rule 1, through the adapter's `cli_copy_out_contents`, with
operands quoted by `shell_word`.

## Rationale

Alternatives considered:

- **Honor a caller `outdir` within an allow-list** (same bucket, or under the experiment). Rejected:
  every consumer would need the same policy, and a permitted neighbour location still lets one run
  register or delete another's data. Identity-derived location makes the question unaskable.
- **Silently override a caller `outdir`.** Rejected: the user would believe outputs went somewhere
  they did not. Refusing at launch is visible and happens before any cost is incurred.
- **Sanitize or deny-list parameter values.** Rejected: values are scientific data (paths, regexes,
  negative numbers) and no deny-list is complete. Quoting each value as one `--key=value` argument
  preserves every value exactly and removes the injection surface instead of filtering it.
- **Drop the shell entirely for Nextflow.** Not chosen: the config prologue is useful in pod logs,
  and quoting everything after a fixed prefix gives the same guarantee.
- **Trust database rows when reaping.** Rejected: a row is history; deletion is authorized against
  the location rule at the time of deletion.

## Consequences

**Positive:** one location rule for writers, collectors, ingest and reapers, so a conforming
component cannot drift from the others; prefix collisions between runs are structurally impossible; launch parameters cannot
redirect outputs or inject shell; cleanup can only delete what lies inside an authorized
location; the rule is provider-neutral because adapters mint and parse all URIs.

**Negative / honest limits:**

- Any `outdir` or `input` in launch parameters is now a 400, including values that would have been
  harmless. A historical run whose stored `outdir` differs from its own location is no longer
  collected, registered or reaped; it is logged and left in place.
- A run with neither an experiment nor a project, or with no resolvable results store, must fail
  closed rather than write to, or silently discard outputs from, a pod-local path.
- Provider CLIs differ in which names they can represent (GCS refuses CR/LF in object names); such
  limits are surfaced as run failures, not hidden.

**Neutral:** a filename policy for uploads is a separate decision; command safety here does not
depend on one.

## References

- `backend/app/platform/run_output_layout.py`, `backend/app/platform/work_dir_layout.py`
- `backend/app/services/run_output_location_service.py`
- `backend/app/platform/nextflow_params.py`, `backend/app/platform/shell_args.py`
- `backend/app/adapters/compute/kubernetes.py` (`_build_nextflow_command`, `_durable_outdir`)
- `backend/app/services/pipeline_run_service.py`, `backend/app/services/custom_pipeline_service.py`
- `backend/app/services/pipeline_monitor_service.py`, `backend/app/cli/register_outputs.py`
- `backend/app/services/work_dir_reaper.py`, `backend/app/services/validation_fetch_reaper.py`
- `backend/app/adapters/storage/{gcs,s3,nfs,local_fs}.py`
