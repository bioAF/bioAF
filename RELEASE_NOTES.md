# Release Notes

## v0.11.3

Point release that automates the GCP quota-increase requests bioAF
needs at install time, plus a small refresh of the shell test suite
that had drifted away from the current `bioaf` and `install.sh`
contracts. No schema changes; no migration required.

### Auto-quota-request in `install-gcp.sh`

Resolves the last open item from to-resolve.md issue #3 (CPUS) and
its 2026-05-07 expansion (SSD / DISKS). Fresh GCP projects ship with
quotas too tight for bioAF to schedule even one pipeline pod
(12 vCPUs, 250 GB regional SSD). On v0.11.2 and earlier, the user
had to discover this via `Pending` pods and `QUOTA_EXCEEDED` autoscaler
events, then go to the console to file an increase manually.

`install-gcp.sh` now adds a "Step 5b: GCP Quota Auto-Request" right
after region selection. It checks each of the three quotas bioAF
needs and, for any that are below target, files a `QuotaPreference`
through the Cloud Quotas API:

- `CPUS-ALL-REGIONS-per-project` &rarr; 64
- `SSD-TOTAL-GB-per-project-region` &rarr; 1024
- `DISKS-TOTAL-GB-per-project-region` &rarr; 2048

On a paid billing account the API auto-approves these in seconds and
the installer reports "granted automatically." On a free-trial
billing account the request goes to human review (1-2 business days)
and the installer surfaces a clear "Google needs to review and
approve this -- this is normal" message before continuing. The
install never aborts on a quota request; if a request was actually
denied, the affected pipeline run will surface the underlying
`QUOTA_EXCEEDED` reason in its log.

The logic lives in `installer/quota.sh` (sourced by `install-gcp.sh`
from a local clone, or fetched over HTTPS when the script is
`curl|bash`'d). Cloud Quotas API errors are surfaced verbatim, so a
4xx response shows the API's `code`/`status`/`message` rather than
just an opaque "request failed."

### Stale `bats` shell tests refreshed

`tests/shell/test_bioaf.bats` and `tests/shell/test_install.bats`
hadn't been touched since the original installer commit, but the
underlying scripts had moved on across many releases. Three tests
were checking for behavior that no longer exists:

- `bioaf help` expected `create-admin`, but admin creation moved to
  the web wizard; the test now checks the actually-current commands.
- `install.sh check-prereqs` expected exit 0 on any host with docker
  and git, but `install.sh` now refuses to run on macOS / Windows by
  design (it is meant for the GCP Linux VM); the test now skips on
  non-Linux hosts.
- `install.sh generate-env` was tested as a "refuse to overwrite"
  gate, but the current contract is "regenerate the file but
  preserve known values (POSTGRES_PASSWORD, SECRET_KEY) unless
  `--force` is passed"; the test now pins that contract.

No production code changed -- this section is test-only cleanup.

### Bug fix: install-gcp.sh exited silently on a Cloud Quotas 4xx

The first version of the auto-quota-request flow (built earlier on
this branch) used `curl -fsSL` and propagated curl's non-zero exit
through the `pref_id=$(bioaf_quota_request_increase ...)` assignment
in the orchestrator. Under `set -euo pipefail` (which `install-gcp.sh`
uses) that aborted the entire installer mid-flow with no user-visible
error, right after printing "Requesting an automatic quota increase
from Google..." Two regression tests now pin this down; the helper
always returns 0 and signals failure via empty stdout, matching the
convention `bioaf_quota_poll` already used.

### Bug fix: missing `contactEmail` rejected every QuotaPreference

After the silent-abort fix surfaced the underlying API error, fresh
projects revealed a second issue: the Cloud Quotas API requires a
`contactEmail` field on every `QuotaPreference` body ("Contact email
must be set in order to increase quota value") and the helper was
not sending it. Long-lived projects can quietly accept submissions
without it because of contacts retained from prior console activity,
which masked the requirement during early testing.

The orchestrator now resolves the gcloud-active account once
(`gcloud config get-value account` -- same identity as the bearer
token, so Google can email the right human if review or denial
happens) and threads it into the body. `(unset)` is treated as empty
so installs without a configured account degrade cleanly rather than
sending a literal `(unset)` string as the contact.

## v0.11.2

Bug-fix point release covering the environment-management gaps and a
work-node Packer-build quota issue surfaced while testing v0.11.1
end-to-end on a fresh greenfield install. No schema changes; no
migration required. The bioaf-base work-node seed is gated by
`BIOAF_BASE_WORK_NODE_IMAGE_URI` and is a no-op until the public
image is published, so existing installs are unaffected.

### Environment pickers correctly filter by type

The Notebooks page and the Workbench Environments "All" filter both
showed environments of the wrong type -- pipeline envs leaked into
notebook pickers and into the workbench list, where they could not
actually be selected. Tracked as to-resolve.md issue #2.

- `notebooks/page.tsx` now requests `/api/v1/environments?type=notebook`.
- The Workbench Environments "All" filter fetches `notebook` and
  `work_node` lists separately and merges them; pipeline envs are
  excluded.

### Default environments seeded at bootstrap

Two related gaps blocked first-time users from launching anything
without manual env creation:

- A default notebook env seeder was missing alongside the existing
  pipeline / work_node seeders; the notebooks picker came up empty.
  Added `ensure_default_notebook_environment` mirroring the pipeline
  seeder.
- Seeders only ran from the lifespan startup hook, which skips when
  org / admin do not yet exist. On a fresh install the user
  completes bootstrap *after* startup, so the seeders never took
  effect until the next backend restart. All three seeders now also
  run from the `create_admin` bootstrap endpoint (idempotent).

### Built-in `bioaf-base` work-node environment (opt-in)

Resolves to-resolve.md issue #1 (backend portion). When
`BIOAF_BASE_WORK_NODE_IMAGE_URI` is set, the backend seeds a
system-managed `bioaf-base` work-node env whose single version is
`status=ready` with `image_uri` pre-populated -- so first-launch is
instant and users no longer have to wait through a ~10-15 min Packer
build before they can pick anything from the work-node environment
dropdown. If the env var is unset, the seed is a no-op and the
existing draft fallback still runs (backward-compatible).

The published Artifact Registry / GCE image itself is not yet built;
once available, set `BIOAF_BASE_WORK_NODE_IMAGE_URI` in the deploy
config to surface the seeded env.

### Build trigger UX

- The "build environment" trigger on `/environments` and
  `/pipelines/environments` no longer uses the bare browser
  `confirm()` dialog (which renders with the host IP in the title
  bar and leaks "Cloud Build" terminology users do not recognize).
  Replaced with the existing `ConfirmDialog` modal; copy now
  explains the build runs in the background and the user can keep
  using the app.
- The build trigger endpoint previously caught only `ValueError`,
  so any other failure (GCP API, credentials, packer step) bubbled
  up as a bare 500 with no JSON body and the frontend rendered a
  meaningless "Unknown error" alert. Now catches broad `Exception`,
  logs the stack server-side, and returns a 500 whose detail is the
  underlying error message so the user sees something actionable.

### Work-node Packer build no longer eats SSD quota

The Packer build VM (transient, runs once per work-node image
build) used `pd-ssd` for its 50 GB boot disk. That 50 GB counts
against the regional `SSD_TOTAL_GB` quota -- already pressured by
the GKE pool nodes' `pd-balanced` boot disks (which also count
toward SSD quota). On a fresh GCP project (default 250 GB
ceiling), one running pipeline pool plus the bioaf control VM
consume ~230 GB; the build's +50 GB tipped past 250 and failed
immediately with `Quota 'SSD_TOTAL_GB' exceeded`.

Switched to `pd-standard` for the build disk -- HDD vs SSD makes
no material difference for a one-off conda env install, and the
image artifact uploaded to GCE Image Service still works for
`pd-ssd` work-node boot disks at launch. A regression test
(`test_packer_template_disk.py`) guards `disk_type` and
`disk_size` so future drift trips a clear failure message.

This is a tactical mitigation; the proper fix (auto-request an
`SSD_TOTAL_GB` quota bump alongside the CPU bump) is tracked in
to-resolve.md issue #3.

## v0.11.1

Bug-fix point release for the v0.11.0 SA hardening work. Resolves the
issues found while testing v0.11.0 end-to-end on a fresh greenfield
install. No new features; no schema changes; no migration required.
Existing installs are unaffected (they continue to use the legacy
`service_account_key` code path).

### Sheets reader SA: now keyless

The v0.11.0 plan filed Breakage 6 ("Sheets reader needs `keys.create`,
which the org policy blocks") as a documented limitation. That
limitation was unnecessary -- Google's permission check is
identity-based, so a doc shared with `bioaf-reader@...` accepts an
impersonated token authenticating as that principal exactly the same
way it accepts a token from a stored JSON key.

- `install-gcp.sh` creates the `bioaf-reader` SA, enables
  `sheets.googleapis.com`, grants `bioaf-app`
  `roles/iam.serviceAccountTokenCreator` on the reader SA, and
  embeds the email in the prefill YAML.
- `get_reader_credentials` returns
  `impersonated_credentials.Credentials` in `vm_default` mode (signs
  via the IAM `SignBlob` API). Legacy
  `service_account_key` installs still use the stored JSON key.
- `create_reader_sa` (in-app fallback) drops `keys.create` entirely
  and writes a `tokenCreator` binding on the new SA.
- `sheets_reader_sa_key` is no longer written to `platform_config`
  on new installs.

Works on policy-enforced projects (`iam.disableServiceAccountKeyCreation`)
because that constraint only blocks `keys.create`, not
`serviceAccounts.create` or `serviceAccounts.getAccessToken`.

### K8s adapters and GCS clients routed through `credential_injector`

The original audit missed three adapters (notebook, compute, cellxgene)
and two GCS-client paths (upload signed URLs, storage stats) that had
their own `_get_gcp_credentials`/`_get_gcs_credentials` helpers calling
`json.loads` on `gcp_service_account_key`. Under SA hardening that row
is empty, so they raised `JSONDecodeError` on first use, blocking
notebook session launch and file upload, and 403'ing the storage stats
query.

- `adapters/{notebooks,compute,cellxgene}/kubernetes.py` now route
  through `credential_injector.load_gcp_credentials(cfg)`.
- `upload_service._get_gcs_credentials` and
  `gcs_storage.GcsStorageService.get_credentials` route through the
  same. v4 signed URLs work because impersonated credentials sign via
  the IAM `SignBlob` API and `tokenCreator` includes `signBlob`.
- `storage_service._query_gcs_buckets` /
  `get_lifecycle_policies` use impersonated bootstrap credentials
  (which have unconditioned `roles/storage.admin`) for project-level
  list operations.

### `gke_cluster_name = "null"` sentinel handling

`stack_deployment.py` writes the literal string `"null"` to
`platform_config` when a Terraform output is empty. The compute
adapter's GKE-metrics call forwarded `"null"` to the GKE API
verbatim and spammed Cloud Logging with `clusters/null`
PERMISSION_DENIED entries. New `_resolve_cfg` helper in
`compute/kubernetes.py` treats the sentinel as missing.
`_k8s_get_cluster_status` raises a clear error;
`_k8s_get_cluster_metrics` returns the safe-zero fallback.

### Pipeline launch reload of cluster_config

A backend that started before compute deploy completed could not
launch pipelines after deploy finished -- it failed with "No GKE
cluster endpoint in platform_config" because the sync K8s helpers
used by `ensure_pipeline_namespace` did not reload config from
`platform_config`. New `_ensure_cluster_config_fresh()` helper is
awaited from every async public entry point in the compute adapter
that uses sync K8s helpers downstream (`_k8s_submit_job`,
`_k8s_cancel_job`, `_k8s_get_job_status`, `_k8s_list_jobs`,
`_k8s_get_job_logs`, `_k8s_persist_job_logs`).

### `roles/monitoring.metricWriter` for the Ops Agent

Added to `bioaf-app`'s unconditioned project bindings in
`install-gcp.sh`, `installer/roles_manifest.yaml`, the backend
`APP_ROLES` fallback, and the frontend role-panel guidance. Same
low-risk profile as `roles/logging.logWriter` (write-only,
cost-only). Stops the Ops Agent's
`MonApiPermissionErr: missing roles/monitoring.metricWriter`
log spam.

### Tests

- 2086 backend tests pass (was 2067 in v0.11.0).
- New `test_k8s_adapter_sa_hardening.py` (6 tests) covers
  credential-injector routing across all three K8s adapters, the
  `"null"` sentinel, and the cluster-config reload guard.
- New `test_upload_credentials_sa_hardening.py` (5 tests) covers
  signed-URL credentials, storage-stats credentials, and graceful
  fallback when credentials are unavailable.
- Existing tests that patched the removed `_get_gcp_credentials`
  helper updated to patch `_load_gcp_credentials` instead.

### Known follow-ups (not in this release)

- Large-file upload UX: the v4 single-PUT signed-URL flow shows "0%"
  for several minutes on large files before progress starts moving.
  Root cause is browser-side `xhr.upload.onprogress` throttling on
  large request bodies. Fix is to switch to chunked resumable
  uploads (the protocol `resumableUpload.ts` already implements for
  reference data).
- Compute Terraform output capture: `gke_cluster_name = "null"` is
  written to `platform_config` even though `outputs.tf` declares
  the output. Read-side normalization stops the spammy errors but
  the upstream capture path needs investigation.
- Cellxgene Workload Identity: cellxgene pods can't authenticate to
  GCS under SA hardening because `_ensure_gcp_secret` writes an
  empty key into a K8s Secret. Proper fix is Workload Identity for
  the cellxgene SA.
- Quota-request UX: pipeline launches fail to schedule on
  CPU-quota-constrained projects with no useful in-app message. The
  pod sits Pending until the user manually requests a CPU quota
  increase in the Cloud Console. Captured for a future feature.

## v0.11.0

Service-account hardening for greenfield installs. Eliminates the JSON
service-account key, splits the broad single-SA into a scoped runtime SA
(`bioaf-app`, attached to the VM) and an impersonated bootstrap SA
(`bioaf-bootstrap`, used only for IAM/Terraform/Cloud Build), and bounds
the runtime SA's blast radius to bioAF-managed resources only via IAM
Conditions, Resource Manager tags, and per-resource bindings.

Existing installs are not migrated -- they keep their JSON-key code path
unchanged. The full design is in
`documentation/sa-hardening/03-consolidated-plan.md`.

### Architecture (greenfield only)

- New `bioaf-bootstrap` SA holds the broad project-level roles formerly
  given to the single SA, minus `roles/iam.serviceAccountKeyAdmin`.
  Impersonated by Terraform, Sheets reader provisioning, and the
  notebook/cellxgene/environment image-build services.
- New `bioaf-app` SA is attached to the GCE VM and holds a small set of
  scoped roles: `roles/storage.admin` (`bioaf-*` buckets only),
  `compute.instanceAdmin.v1` (`bioaf-*` VMs only), `container.admin`
  (resources tagged `bioaf-managed=true`), the project-scoped custom
  role `bioafSaManager`, plus `roles/iam.serviceAccountTokenCreator`
  resource-scoped to `bioaf-bootstrap` only.
- Project-scoped Resource Manager tag `bioaf-managed=true` attached to
  bioAF-managed GKE clusters; per-secret and per-subscription bindings
  for `bioaf-app` rendered by Terraform.
- New platform_config key `gcp_bootstrap_sa_email`, persisted at startup
  from VM instance metadata (`bioaf_bootstrap_sa_email`). The
  credential injector and image-build services prefer it over the
  legacy `gcp_service_account_email` so existing keyed installs keep
  working.

### Installer

- `install-gcp.sh` creates both SAs, the `bioaf-managed` tag, the
  custom IAM role, the conditioned bindings, and the resource-scoped
  tokenCreator binding. Attaches `bioaf-app` to the VM and writes the
  bootstrap email into VM metadata.
- The legacy "create SA + JSON key + paste worksheet" step is removed.
- New file: `installer/roles_manifest.yaml` -- single source of truth
  for both SAs' permissions, read by both the installer and the
  backend validation probe.

### Backend

- `credential_injector.load_gcp_credentials` reads `gcp_bootstrap_sa_email`
  first and falls back to the legacy email field.
- `notebook_image_service`, `cellxgene_image_service`, and (transitively)
  `environment_build_service` now obtain credentials via the injector
  so impersonation reaches Cloud Build / Artifact Registry.
- `terraform_executor` injects the bootstrap email into the env dict
  before `build_env`. The Terraform tfvars writer plumbs
  `bioaf_app_sa_email` and `bioaf_bootstrap_sa_email` through to the
  storage and compute modules.
- `gce.py` work-node adapter routes credentials through the injector
  (no more "no JSON key" hard error on greenfield) and drops the
  legacy `gcp_service_account_email` fallback for the VM-attached SA.
- `gcp_config.validate_gcp_credentials` runs a dual-SA probe in
  `vm_default` mode: bioaf-app via raw ADC, bioaf-bootstrap via
  impersonation. Merged result requires both. New
  `app_probe`/`bootstrap_probe` fields on `GCPValidationResult`.
- `sheets_reader_sa_service` surfaces a clear error when `keys.create`
  fails because the project enforces
  `iam.disableServiceAccountKeyCreation`.
- `main.py` lifespan reads `bioaf_bootstrap_sa_email` from VM metadata
  and persists it on first startup (idempotent; skipped outside GCE).

### Terraform

- Per-secret `roles/secretmanager.secretAccessor` bindings for
  bioaf-app (gated on `bioaf_app_sa_email`).
- Per-subscription `roles/pubsub.subscriber` bindings for bioaf-app on
  the ingest worker + dead-letter subscriptions.
- `bioaf-managed=true` tag binding attached to the GKE cluster in both
  the legacy top-level module and the backend `compute` module.

### Frontend

- Setup wizard and GCP settings page replace the hardcoded 14-role
  list with two adjacent panels: bioaf-bootstrap roles (broad) and
  bioaf-app roles (scoped). Validation result shows per-SA pass/fail
  cards when the new probe fields are present; falls back to the
  legacy single list for keyed installs.

### Tests

- `test_credential_injector` extended with three vm_default impersonation
  cases (new key, legacy fallback, neither set).
- `test_terraform_executor` extended to verify `_read_gcp_config`
  selects `gcp_bootstrap_sa_email` and `run_plan` passes it through.
- New: `test_sheets_reader_sa_service`,
  `test_image_build_credentials`, `test_gce_adapter`,
  `test_gcp_config_dual_probe`, `test_bootstrap_metadata`,
  `test_roles_manifest`.
- New CI invariants: `test_bucket_naming_invariant` (every
  `google_storage_bucket` starts with `bioaf-`),
  `test_compute_naming_invariant` (every Python `instance_name = '...'`
  starts with `bioaf-`), `test_gke_tag_invariant` (every
  `google_container_cluster` file declares a sibling
  `google_tags_tag_binding`).

### Documented limitation

- Sheets integration cannot be enabled on projects that enforce
  `iam.disableServiceAccountKeyCreation` because the Sheets reader SA
  still requires `keys.create`. The setup wizard now surfaces a clear
  message on enable rather than a stack trace.

## v0.10.3

Reference Data Ingest — completes the four user-facing capabilities of ADR-017 / ADR-047 (upload, import-from-URL, versioning, and pipeline linkage). Existing reference data CRUD is unchanged; this release adds everything around getting bytes into the registry and using them in pipelines.

### New features

- **Reference upload** -- drag-drop multi-file upload page at `/data/references/new`. Bytes go directly to GCS via resumable session URLs (8 MiB chunks; 64 MiB for files > 1 GiB) so 30+ GB CellRanger references survive flaky lab Wi-Fi
- **Import from URL** -- separate page at `/data/references/import` for pulling references from public sources (GENCODE, 10x, etc.). A per-import GKE Job streams the source into GCS, supports `none`/`gzip`/`tar`/`tar.gz` extraction modes, and reports progress via a polling endpoint
- **Versioning UX** -- reference detail page gets a Versions tab listing every `(name, category)` sibling, with the current row highlighted and deprecated rows dimmed. New "Upload new version" button pre-fills name + category + scope and locks them so only version + files differ
- **Reference parameter type for custom pipelines** -- custom pipelines can declare a parameter as `variable_type='reference'` with a `reference_category` (`genome`/`annotation`/`index`/`atlas`/`markers`/`other`/`any`). At launch, those parameters render a searchable dropdown of active references in that category; the selected dataset's path is stored in run parameters so the existing auto-linker picks it up
- **Linked references on run detail** -- the "References Used" table on `/pipelines/runs/[id]` adds a Category column and turns each reference name into a link back to its detail page

### New endpoints

- `POST /api/references/upload-init` -- create a reference in `status='uploading'` and return per-file GCS resumable session URLs
- `POST /api/references/{id}/upload-complete` -- list the GCS prefix, verify every declared file arrived, persist md5 + size, flip status (`internal -> active`, `public -> pending_approval`, mismatch -> `failed` with prefix purge)
- `POST /api/references/{id}/abort` -- purge GCS objects and delete the reference row (idempotent)
- `POST /api/references/import` -- launch the importer GKE Job
- `GET /api/references/{id}/import-status` -- read progress (`pending`/`downloading`/`verifying`/`extracting`/`finalizing`/`active`/`failed`)
- `POST /api/references/{id}/import-cancel` -- terminate the GKE Job and abort the reference
- `GET /api/references/by-name?name=...&category=...` -- return every version for a `(name, category)` tuple in one round-trip
- `POST /api/internal/references/{id}/import-progress` -- importer-container callback authenticated by `X-Internal-Token` (settings.internal_token); the auth middleware exempts `/api/internal/*` so the container can reach it without a user JWT

### Roles & permissions

- New `references` resource with `view` and `upload` actions. Migration 071 backfills both for `admin`/`comp_bio` and `view` for `bench`/`viewer` on existing system roles. Existing endpoints unchanged; new endpoints use `references:upload`

### Database (additive only)

- Migration 071: backfill `references:view`/`upload` permissions
- Migration 072: `reference_import_progress` table tracking GKE-job-driven imports (PK `reference_id`, cascade delete)
- Migration 073: `custom_pipeline_variables.reference_category` column
- `REFERENCE_STATUSES` extended with `uploading` and `failed`

### Infrastructure

- Terraform `storage` module gains a `bioaf-references-{org_slug}-{stack_uid}` bucket with versioning + CORS for browser PUT/POST
- New platform_config key `references_bucket_name`, populated by the storage stack on apply
- New env var `BIOAF_INTERNAL_TOKEN` for the importer-callback secret

### Spec

- `documentation/spec-reference-data-ingest.md` is the source of truth for this release

## v0.10.2

Point release adding per-pipeline QC dashboard configuration. Existing scRNA-seq dashboards render identically; new templates plug in by shipping a config + extractor instead of forking the dashboard page.

### New features

- **Per-pipeline QC templates** -- pipelines now declare a `qc_template` (`scrnaseq`, `bulk_rnaseq`, or `custom`) and may carry a `qc_config_json` override. The QC dashboard reads sections, metric labels, formats, thresholds, and chart specs from that config instead of hardcoded scRNA-seq logic
- **Custom-pipeline QC dashboards** -- custom pipelines that emit `/outputs/qc_metrics.json` get a real QC dashboard rendered from the version's `qc_config_json`. Both fields are versioned with the pipeline (per ADR-033 immutability) so editing the layout produces a new version
- **QC dashboard config in the pipeline editor** -- new collapsible "QC dashboard config" panel on the custom-pipeline version form: template select + JSON textarea with client-side parse + object-shape validation
- **Generic QC dashboard renderer** -- the QC dashboards page replaces its hardcoded scRNA-seq body with a config-driven `<GenericQCDashboard/>`. Sections, metric cards, formats, threshold colors, and charts all dispatch off `qc_config`
- **Reproducibility snapshot** -- each generated dashboard row stores the resolved render config, so old runs always render the way they were generated even after a pipeline's config changes later

### New documentation

- **`docs/guides/custom-pipelines.md`** -- end-to-end guide to authoring custom pipelines: prerequisites, runtime contract, variables, version cascade, permissions
- **`docs/guides/custom-qc-config.md`** -- reference for the QC config schema (sections, metrics, formats, thresholds), how to emit `qc_metrics.json`, and a hello-world example

### Backend

- New columns on `pipeline_catalog`, `custom_pipeline_versions`, and `qc_dashboards` (additive migration 070) for `qc_template` + `qc_config_json`
- New `app/services/qc/` package: per-template extractors + render configs (`scrnaseq`, `bulk_rnaseq`, `custom`), a resolver that walks run -> custom-pipeline-version -> catalog-entry -> default fallback, and a shared GCS helpers module
- `QCDashboardService` is now a thin orchestrator that dispatches via the template registry; existing `_read_*` helpers remain on the class as backwards-compat shims
- `QCDashboardResponse` gains `qc_config` and `raw_metrics` fields. Pre-snapshot rows substitute the resolved template default on read so legacy dashboards still render
- `CustomPipelineVersionCreateRequest` + `Response` carry `qc_template` + `qc_config_json`; Pydantic enforces the JSON-object shape

## v0.10.1

Point release tightening file upload, association, and provenance display.

### Fixes

- **Drag-and-drop uploader accepts any file type** -- the FASTQ/h5ad/CSV/TSV allowlist silently dropped legitimate files (protocols, READMEs, analysis exports)
- **File search inherits through samples** -- searching by experiment now returns files attached to that experiment OR to any of its samples; searching by project pulls in files at every level beneath. Sample-level pipeline outputs no longer hide from the experiment view.
- **File tiles show full provenance** -- each row renders a `Project > Experiment > Sample > Pipeline Run #N` (or `... > Notebook` / `... > Work Node`) breadcrumb under the filename, with the resolved creator (uploader or pipeline/session launcher) in its own column
- **Explicit Global scope on upload** -- the upload page now requires picking Global / Project / Experiment / Sample; Global files render a distinct badge so they are not confused with truly unassociated files
- **Files page matches Experiment > Files** -- the Data & Files > Files page now uses the same FileBrowser layout as the experiment-scoped Files tab
- **Documents page removed from Data & Files menu** -- file handling is consolidated under Files

### Backend

- New `is_global` column on `files` (additive migration 069)
- `FileService.list_files` cascades inheritance through `sample_files` for both experiment and project filters
- `FileService.get_provenance_for_files` returns batched project/experiment/sample/pipeline-run/compute-session/creator data per page

## v0.10.0

Custom pipelines: define and run user-authored pipelines (bash, Python, Perl, R, etc.) against tracked input data with full provenance, versioned definitions, and Conda-based environments.

### New features

- **Custom Pipelines** -- author pipelines in any language by combining a script, command, and pipeline environment; runs execute as K8s Jobs with input mounts, output collection, and report detection (ADR-044)
- **Pipeline Environments** -- new "Pipeline" environment type with conda-only Docker build routing, separate from Notebook and Work Node environments; managed from a new Pipelines > Environments page (ADR-045)
- **Versioned pipeline definitions** -- each save creates a new pipeline version with its own script, command, variables, and pinned environment version; runs always reference the version they launched against
- **Pipeline variables** -- declare typed variables (string, number, file, sample) on a pipeline; values are validated and delivered as environment variables and a `params.json` manifest at runtime
- **Version cascade** -- rebuilding a pipeline environment automatically creates new minor versions of any pipelines that pin it, via an event-bus-driven cascade handler (ADR-046)
- **Custom pipeline catalog integration** -- the pipeline catalog now lists custom pipelines alongside nf-core entries, with creator and latest-version metadata surfaced on each card
- **Custom pipeline launch dialog** -- type-aware launch flow that renders variable inputs (including file/sample pickers) and submits to the custom-pipeline endpoint
- **Run detail for custom pipelines** -- run detail page renders the pipeline-supplied report (HTML or markdown) and the captured log file, with project/experiment links pulled from launch context
- **Project-scoped outputs** -- custom pipeline outputs register against the launching project (and experiment when applicable) with `pipeline_output` source type and full provenance back to the pipeline version

### Backend

- New models: `CustomPipeline`, `CustomPipelineVersion`, `CustomPipelineVariable`; pipeline_runs gains `custom_pipeline_version_id` and `output_files_json` columns (migration 068)
- `CustomPipelineService` covers CRUD, version management, launch orchestration, manifest building, and output sync
- Kubernetes compute adapter learns to launch custom-pipeline jobs with conda activation, input staging, output collection to GCS, and report artifact detection
- Pipeline monitor handles custom-pipeline run lifecycle: status transitions, log/report retrieval, and output registration via `_handle_completion`
- New API router `app/api/custom_pipelines.py` with permissions `custom_pipelines:create|read|update|delete|launch`, seeded into the four built-in roles

### Frontend

- New pages: Pipelines > Custom (list), Pipelines > Custom > [id] (detail with versions, variables, runs), Pipelines > Environments
- `CustomPipelineLaunchDialog` reuses `FileTreeSelector` for file/sample variable inputs
- Run detail page renders the report and log produced by the pipeline; navigation gains a Pipelines > Environments entry

## v0.9.0

Work Nodes overhaul: GCE VMs with conda environments, GitHub repo cloning, and a redesigned file picker.

### New features

- **Work Nodes on GCE VMs** -- work nodes now launch as full Linux VMs on Google Compute Engine instead of GKE Pods, with SSH access via session credentials (ADR-043)
- **Packer-built VM images** -- work node environments build as GCE VM images via Cloud Build + Packer with conda environments pre-installed for fast startup
- **Independent environment types** -- environments are now tagged as "Notebook" or "Work Node" with separate image pipelines; work node environments are conda-only
- **GitHub repo cloning** -- users manage a list of GitHub repos on the Work Nodes page; selected repos are automatically cloned into `~/repos/` when a work node boots
- **MOTD** -- work nodes display a message of the day on SSH login showing paths to input data, repos, outputs, and scratch space
- **File picker for work nodes** -- the launch wizard now uses the same FileTreeSelector as notebooks, with project -> experiment -> file selection and sample grouping
- **Default Work Node environment** -- a base conda environment (Python 3.11, numpy, pandas, scipy, matplotlib, etc.) is automatically seeded on first boot
- **Files page search and filters** -- added filename search, project filter, experiment filter, and source type filter to the Data & Files page
- **Work node output tracking** -- outputs from work nodes are registered as `work_node_output` source type, distinct from notebook outputs

### Improvements

- **Zone retry on capacity exhaustion** -- VM creation tries zones b, c, f, a in order if GCE returns ZONE_RESOURCE_POOL_EXHAUSTED
- **E2 machine types** -- added e2-standard-4, e2-standard-8, and e2-highmem-8 with better availability than N2 in constrained regions
- **Resource failure UX** -- failed launches due to GCP capacity show "Resource Failure" status with a "GCP Resources Unavailable" detail banner instead of generic "failed"
- **Stop persistence** -- stopping a work node immediately commits a "stopping" status so navigating away no longer shows stale "running" state
- **Project file filter** -- the project filter on the Files page now includes files associated via experiment, not just direct project_id

### Bug fixes

- Fix Packer template syntax (double braces, missing packer init, universe repo, miniconda TOS)
- Fix SSH access (password auth via sshd drop-in config, username in SSH command)
- Fix output sync (SSH into VM before stopping instead of unreliable shutdown hooks)
- Fix environment rebuild for work node type (was hardcoded to Dockerfile format)
- Add google-cloud-compute dependency for GCE adapter

## v0.8.3

Fixes fresh install failure and improves the GCP installer experience.

### Bug fixes

- Fix setup failing on fresh VMs with "vunknown" image tag by adding a grep/sed fallback for Python < 3.11 (Ubuntu 22.04 ships Python 3.10 without tomllib)
- Setup now queries GitHub releases for the latest version with available images, walks back through recent releases if needed, and falls back to building from source as a last resort

### New features

- Add `--version` flag to `./bioaf setup` for installing a specific version (e.g., `./bioaf setup --version 0.8.1`)
- Add a Setup Worksheet section to the GCP installer output with the project ID, region, and service account JSON key highlighted in green for easy copy-paste

## v0.8.2

Update flow improvements for faster updates with less downtime.

### Bug fixes

- Pull pre-built images before restarting containers so the app stays online during the download, reducing downtime to just the restart + migrate window
- Move Cloud Logging agent setup to after restart so it does not block the update while the app is down
- Show restart countdown message in the CLI so users know why the update pauses before restarting

## v0.8.1

Pre-built Docker images published to GitHub Container Registry on each release. This is the first version to ship remote artifacts -- setup and updates now pull pre-built images instead of building on the VM, reducing install and update time from 15+ minutes to seconds. Users who encounter issues can install from source using v0.8.0 and below as they have to this point.

### New features

- Publish backend, frontend, and cellxgene Docker images to ghcr.io on each release, with GHA build cache for fast CI builds
- Setup and update commands pull pre-built images from ghcr.io instead of building locally on the VM
- Frontend image build validation added to PR CI pipeline

### Platform updates

- Add OCI source labels to Dockerfiles for automatic ghcr.io repository linking
- Add `image:` directives to docker-compose.yml alongside `build:` directives, supporting both pull (production) and build (development) flows
- Move Cloud Logging agent setup from start to setup/update only, eliminating a 5+ minute apt-get delay on every restart

## v0.8.0

Google Sheets integration for experiment field setup and sample import.

### New features

- Import column headers from a Google Sheet during experiment creation to populate sample field defaults and custom fields, with a column mapping UI that lets users map sheet columns to existing fields or create new custom fields
- Import sample data directly from a Google Sheet using the same preview, column mapping, and confirm flow as CSV import
- Dedicated reader service account provisioned automatically via the IAM API, managed from Settings > Integrations > GCP
- Auto-match unknown columns to existing custom fields when names match, so repeat imports pre-select the right mapping
- All 19 user-facing sample fields are now recognized during import (not just the 10 defaultable ones), with visual indicators distinguishing fields that support defaults from per-sample fields

### Platform updates

- Add `iam.serviceAccountKeys.create` permission and `roles/iam.serviceAccountKeyAdmin` role to the GCP setup checklist, install script, settings UI, and setup wizard
- Add `google-api-python-client` dependency for Sheets API v4 and IAM Admin API access

### Bug fixes

- Fix IAM propagation race condition when creating the reader service account key immediately after SA creation
- Fix dropdown collision where "Add as new custom field" and "Map to existing custom field" had identical select values when the column name matched an existing field name
- Distinguish "Sheets API not enabled" from "sheet not shared" errors so users get actionable guidance

## v0.7.5

Plot Archive thumbnail bug fix.

### Bug fixes

- Restore Plot Archive previews that broke after the content-token JWT rework. Thumbnails rendered an empty `<img src="">` while the content token was still being fetched, which fired `onError` and latched the card into the "No preview available" fallback. The grid now shows a skeleton until the short-lived content URL is ready.

## v0.7.4

In-app update UX improvements.

### Platform updates

- Add a 60-second "restart warning" step between build and restart, giving users a visible countdown before the backend briefly goes offline during an update
- Settings > Platform Info re-attaches to an in-progress update on page mount, so navigating away and back shows live status instead of an empty banner
- Countdown duration is configurable via BIOAF_RESTART_WARN_SECONDS and skippable with BIOAF_SKIP_RESTART_WARN=1 for development

## v0.7.3

OOM detection, preemption classification, and cluster configuration UX improvements.

### Pipeline monitoring

- Detect OOM-killed pipelines from K8s container termination reasons and set failure_reason to "oom" with actionable guidance
- Detect Spot preemption exhaustion from failed process exit codes (143/137/247) and set failure_reason to "preemption_exhausted"
- Emit PIPELINE_OOM event (critical severity) for notification routing
- Store exit_code on PipelineProcess records from adapter progress data

### Infrastructure UI

- Reorganize cluster config panel into "Pipeline Nodes" and "Interactive Nodes" columns
- Add Spot instance info tooltip explaining cost savings and auto-retry behavior
- Remove pt-5 alignment hack on the Spot toggle

### Pipeline run UI

- Show amber OOM banner with "Update node size" link on run detail page
- Show blue preemption banner with "Re-run" and "Disable Spot" actions on run detail page
- Add orange "OOM" and blue "Preempted" badges on the pipeline runs table

### Database

- Add nullable failure_reason column to pipeline_runs (migration 066)

## v0.7.2

Security hardening from external pentest, deployment and setup reliability fixes.

### Security

- Reject known-insecure JWT secret keys at startup
- Disable OpenAPI docs and Swagger UI in production
- Remove smtp_configured from unauthenticated bootstrap status response
- Require authentication for /api/health/services and /api/health/status
- Replace session JWTs in file/plot content URLs with short-lived scoped tokens (60s TTL)

### Deployment

- Fix GCP zone fallback for regions without a "-a" zone (us-east1, europe-west1)
- Reduce GKE default node pool disk to 30GB to stay within default SSD quota
- Zone fallback in install-gcp.sh retries all zones before failing
- Unique service account name per install to avoid stale IAM bindings
- Role grant errors are now reported instead of silently swallowed
- User-friendly error message for GCP quota failures during deploy
- Add missing google-cloud-iam dependency for orphaned resource cleanup

### Setup Wizard

- Block advancement when GCP validation fails, show results inline
- Surface terraform error details instead of generic "Apply failed"
- Log terraform init exceptions with full traceback

### Fixes

- Fix filename collision when uploading multiple files to same sample
- Fix React hooks violation in PlotThumbnail component
- Run history table shows error messages for failed operations

## v0.7.1

Fix validation error display in setup wizard and all API error surfaces.

### Fixes

- Pydantic 422 validation errors (e.g. invalid org slug) now display the actual error message instead of "[object Object]"
- Applies to all API calls: fetchApi, uploadFile, and downloadFile

## v0.7.0

Security hardening and deployment reliability improvements from external pentest findings.

### Security

- Reject known-insecure JWT secret keys at startup (prevents running with default/public secrets)
- Disable OpenAPI docs and Swagger UI in production (404 instead of serving 376-endpoint schema)
- Remove smtp_configured from unauthenticated bootstrap status response
- Require authentication for /api/health/services and /api/health/status endpoints
- Replace full session JWTs in file/plot content URLs with short-lived, resource-scoped tokens (60s TTL)
- New POST /api/content-tokens endpoint for issuing scoped content access tokens

### Deployment Fixes

- Fix GCP zone fallback for regions without a "-a" zone (us-east1, europe-west1)
- Add backend region-to-zone mapping with correct zones for all 17 supported regions
- Setup wizard now blocks on GCP validation failure and displays missing permissions inline
- Fix zone fallback in frontend settings and setup wizard

## v0.6.6

Automated backup scheduling and timezone fixes across the platform.

### Backup Scheduling

- Backups now run automatically on a user-configured schedule instead of requiring manual triggers
- Enable/disable toggle per tier (PostgreSQL and platform config)
- Set first backup to "now" or a specific future date/time
- Configurable cadence (hours between backups) and retention (days to keep)
- Background loops poll every 60 seconds and execute when a backup is due
- Backups older than the retention period are automatically deleted from GCS
- Add config backup background loop (was previously missing)

### Fixes

- Fix backup settings not persisting across page refreshes (missing transaction commit)
- Fix scheduled backup time shifted by timezone offset (naive local datetime treated as UTC)
- Standardize all date/time display to user's local timezone across the platform

## v0.6.5

Installability improvements: one-command GCP setup, versioned updates from CLI and UI.

### GCP Installer

- Add `install-gcp.sh` for one-command GCP provisioning (VM, firewall, service account)
- Script installs gcloud CLI if needed, creates an e2-medium VM with Docker pre-installed
- Waits for SSH and Docker readiness before presenting next steps
- Optionally creates a service account and prints the JSON key for the setup wizard

### Update System

- `./bioaf update` now accepts an optional version argument (e.g., `./bioaf update 0.7.0`)
- Backs up the database before every update
- Fetches and checks out the target git tag instead of following a branch
- Writes progress status for the UI to track
- Add host-side update agent (systemd service) that watches for trigger files from the backend
- Add "Install Update" button on Settings > Information page with real-time progress display
- Backend resolves pending upgrades on startup (marks completed or failed)

### Fixes

- Fix `get_access_url` on GCE to query metadata server for external IP instead of showing internal 10.x address
- Auto-activate docker group via `sg docker` instead of requiring re-login after VM setup
- Fix bash 3.2 compatibility in installer (macOS ships bash 3.2)

### Housekeeping

- Remove orphaned `frontend/src/app/admin/` pages (all had permanent redirects)
- Update deployment guide, ADR-005, README, and user guides for accuracy

## v0.6.4

Plot archive bug fixes, PDF thumbnail generation, and detail modal improvements.

### Bug Fixes (Issue #151)

- Add unique constraint on `platform_config.key` to prevent duplicate rows that broke the plot archive scanner
- Fix scanner using bare ADC instead of app service account credentials
- Add SVG (`image/svg+xml`) and PDF (`application/pdf`) content-type mappings to file content endpoint
- Preserve click handler on plot thumbnails that fail to load

### PDF Thumbnails

- Render PDF page 1 to PNG thumbnails using PyMuPDF, stored under `_thumbnails/` prefix in the results bucket
- Scanner auto-generates thumbnails when indexing new PDF plots
- Add `GET /api/plots/{id}/thumbnail/content` endpoint for serving thumbnail bytes
- Extend `POST /api/plots/backfill` to generate missing thumbnails for existing PDFs
- Clean up thumbnail blobs from GCS when associated files are deleted
- Offload PDF rendering to thread pool to avoid blocking the event loop

### Plot Detail Modal

- Rework modal to match Data & Files detail layout with metadata grid and download button
- Display project, experiment, pipeline, session, source, and indexed date
- Add file format badge (PNG, SVG, PDF) to thumbnail cards
- Standardize card title font sizing

## v0.6.3

Infrastructure lifecycle stability, Cloud Logging, and deploy UX improvements.

### Cloud Logging

- Auto-detect GCE and attach Cloud Logging using the app's configured service account
- Install Ops Agent via `./bioaf start` for Docker container log collection
- Add `logging.logEntries.create` to GCP validation permission checks

### Infrastructure Lifecycle

- Replace 30-minute hard timeout with in-memory process registry so GKE deploys run to completion
- Fix lock file deletion to use app credentials instead of ADC
- Fix orphaned resource cleanup returning 404 for valid resources
- Expand orphan detection and cleanup to cover IAM service accounts
- Deduplicate orphaned resource entries across repeated failures
- Add GKE cluster and service account scanning via GKE/IAM APIs
- Persist tfvars on each TerraformRun for audit and reproducibility (migration 064)

### Deploy UX

- Show full planned resource list in deploy modal from the start (Queued/Setting up/Done states)
- Move teardown and storage destroy to background endpoints with polling
- Add region/zone selection at deploy time with cross-region cost warning
- Fix empty modal when no active run (idle state with "Starting operation...")
- Fix modal stuck after operation completes (terminal status persistence)
- Visible scrollbar on resource list

## v0.6.2

Audit log coverage gaps and activity feed event fixes.

### Audit Log Coverage (closes #153)

- Add logout endpoint (POST /api/auth/logout) with audit logging
- Log failed login attempts with reason (invalid credentials, account deactivated)
- Log file content serving as download audit entries
- Change role update audit action from generic "update" to "role_change" with old/new role names
- Log environment build success and failure from the build poller
- Log postgres and config backup completion and failure
- Log quota exceeded events alongside event bus emission
- Log notebook session access (who opened which session)
- Normalize download action name from "downloaded" to "download"
- Update audit log page filters with new entity types and actions
- Color-code new action badges (failures red, success green, warnings amber, role changes purple)

### Activity Feed Fixes

- Add PIPELINE_STARTED event type, emitted on successful run submission
- Emit PIPELINE_COMPLETED and PIPELINE_FAILED from pipeline monitor completion handler (event types existed but were never fired)
- Fix AUTO_RUN_BUDGET_DISABLED payload using wrong key ("organization_id" instead of "org_id"), silently dropped by NotificationRouter
- Fix AUTO_RUN_LAUNCHED payload missing org_id, user_id, and all display fields
- Frontend logout now calls backend endpoint so audit log entry is created

## v0.6.1

Pipeline run cost estimates based on actual GCP instance pricing.

### Cost Estimates

- Store cost estimate from compute adapter when launching a pipeline run (closes #203)
- Replace flat-fee stub with actual hourly spot rate for the pipeline node pool (n2-highmem-16)
- UI column renamed from "Cost" to "Est. $/hr" to clarify that values are hourly node rates, not totals

## v0.6.0

Automatic pipeline runs triggered by sample completeness, manifest reconciliation fixes, pipeline execution fixes, and UI cleanup.

### Auto-Run Pipelines

- Configure pipelines to run automatically when all expected files for a sample arrive and pass MD5 verification
- New ExperimentAutoRun and PendingAutoRun models with API endpoints for CRUD and status
- Background loop launches pending runs after configurable delay
- Auto-run evaluation integrated into the manifest ingest flow
- Replaced old trigger infrastructure (trigger_service, pipeline_triggers) with the new auto-run system

### Manifest Ingest Fixes

- Fix race condition where files arriving before the manifest were never linked to samples
- Retroactive reconciliation: when a manifest arrives, match already-ingested files by MD5 + filename + org + 2-hour time window
- Content-aware redelivery guard: compare incoming manifest entries against existing ones instead of just checking for existence
- Forward-path query now prefers MD5+filename match, falls back to filename-only for checksum mismatch detection
- Shared reconcile_manifest_entry() helper eliminates duplication between forward and retroactive paths

### Pipeline Execution Fixes

- Re-enable Fusion for GCS-backed pipeline runs (was incorrectly made opt-in, breaking all K8s process pods)
- Fix trace parser reading wrong column for process names ("process" vs "name" in Nextflow trace.tsv)
- Fix Nextflow K8s executor test to match Fusion-always-on behavior

### Pipeline Run UI

- Show pipeline logs directly without process dropdown for K8s runs (single log, no selection needed)
- Auto-detect protocol from sample chemistry_version, remove manual CV dropdowns from launch wizard
- Add bulk sample deletion with confirmation modal

### Navigation and Settings

- Remove unused Pipeline Scheduling placeholder page
- Move Naming Profiles from Settings to Data & Files section
- Consolidate GCP, SMTP, and Slack settings into Settings > Integrations with tabbed layout
- Add Seqera tab with coming-soon placeholder for Fusion license support

## v0.5.5

Auto-ingest pipeline hardening and manifest-driven file association. Groundwork for the upcoming auto-run pipeline feature.

### Auto-Ingest Fixes

- Pass stored GCP service account credentials through all downstream GCS operations (manifest reads, file copies, cleanup deletes)
- Fix double-delete: skip cleanup when move_file already deletes the source
- Fix duplicate manifest entries on Pub/Sub message redelivery
- Fix ManifestEntry reconciliation when duplicate pending entries exist
- Convert base64 MD5 from GCS Pub/Sub to hex for manifest checksum comparison
- Move manifest reconciliation before file copy so resolved experiment IDs determine the GCS prefix

### Manifest-Driven Sample Linkage

- Derive experiment and project from resolved samples in manifest ingest
- Create sample_files junction rows during file ingest for manifest-resolved samples
- Set file.experiment_id from manifest resolution so files appear in the correct experiment
- Add batch-position mapping via sample_index (S-number) segment in naming profiles

### UI

- Replace Sample Batch, Seq. Batch, and Pos. columns on the samples table with a Files count column
- Fix CSV upload custom field storage and mapping
- Fix auto-ingest settings save and listener restart behavior

### Housekeeping

- Rename sample_id_external to sample_id_unique across the codebase (DB column unchanged, additive-only)
- Fix file deletion blocked by manifest_entries FK constraint
- Fix serialize_entity to handle attribute/column name mismatches

## v0.5.4

Bug fix for database restore and UI cleanup on the Backup & Recovery page.

- Fix `_build_restore_url()` mangling database credentials when the PostgreSQL username contains "bioaf" (caused auth failures after restore swap)
- Replace browser `confirm()` dialogs with in-app ConfirmDialog on all backup restore/accept/reject actions

## v0.5.3

Setup wizard overhaul and installer improvements.

### Setup Wizard

- Setup flow now starts with a terminal-issued setup code that proves host access, replacing the old email verification step
- Wizard steps reordered: setup code, admin creation, org name, GCP credentials, SMTP, infrastructure decision, stack selection
- "Skip for now" buttons renamed to "Do this later" throughout
- Infrastructure step is a decision fork: deploy now or configure later
- Infrastructure init button shows processing state during terraform setup
- Removed team invite step from the wizard (available later from Settings)
- Price estimate removed from Kubernetes + GCS card

### CLI

- `./bioaf setup` now auto-runs the installer when `.env` or TLS certs are missing, so users can go from `git clone` to `./bioaf setup` in one step
- `./bioaf setup` prints the one-time setup code in green with the login URL
- macOS and Windows are detected early with a message pointing to the GCP setup docs
- `./bioaf create-admin` deprecated in favor of the web-based setup wizard

### Backend

- New `SetupCodeService` generates 6-character alphanumeric codes (bcrypt hashed, 1-hour TTL, single-use)
- New bootstrap endpoints: `generate-setup-code` and `verify-setup-code`
- `create-admin` endpoint now requires a setup JWT instead of being fully open
- Bootstrap status endpoint returns `has_setup_code` and `has_admin` fields
- Non-streaming `POST /api/v1/infrastructure/terraform/init` endpoint for the setup wizard
- Migration 061 adds `setup_code_hash` and `setup_code_expires_at` to organizations

### Getting Started (stubbed)

- 13-slide onboarding component with highlight overlays built but not yet linked
- Screenshots from marketing site included as placeholders, will be recaptured from the running app
- Route and component exist at `/getting-started` but are not accessible from the UI

## v0.5.2

Batch UX rework, custom fields, and entity snapshots.

### Batch UX

- Batches are now text fields on samples with find-or-create behavior instead of separate management pages with ID assignment
- Sample batches scoped per experiment, sequencing batches scoped per organization
- Batch codes added to sample field defaults at experiment creation
- CSV upload columns renamed to user-facing `sample_batch` and `sequencing_batch`
- Batches tab renamed from "Sample Batches" and counter removed

### Custom Fields

- Custom fields section on experiment create always visible (no longer gated behind template selection)
- Template-driven custom fields auto-populate; users can add arbitrary fields on top
- Custom fields support `is_required` flag with migration 059
- Custom fields editable on experiment detail page overview
- Experiment custom fields now inherited by samples as per-sample values (migration 060, new `sample_custom_fields` table)
- Sample create/edit forms render experiment custom field inputs
- Sample view modal displays custom field values

### Entity Snapshots

- Entity snapshots model and migration for point-in-time metadata capture
- Snapshot integration into audit service with optional snapshot parameter

### Manifest-Driven Ingest (foundation)

- Sequencing batch and manifest entry models with API
- Manifest parsing service for md5sum and CSV formats
- Manifest retry service for pending file verification
- Activity feed logging for manifest ingest events
- Sample completeness trigger and trigger_on schema field
- Auto-ingest manifest configuration UI
- This lays the groundwork for pipeline automation but does not finalize it

### Other

- Restored dropped columns on `sample_batches` (instrument model, platform, quality score encoding, sequencer run ID)
- GEO export reads instrument from sequencing batch
- Dropdown widths in field defaults now match text input widths

## v0.5.1

Improve notebook file selection UX.

- Files in the notebook launch picker are now sub-grouped by GCS subdirectory path (e.g., star/001/Gene/filtered vs star/001/Gene/raw), so identically named files from different pipeline stages are clearly distinguishable
- Each file shows a source type badge (Pipeline, Notebook, Upload) and creation date
- Files linked to a sample no longer duplicate under "Experiment Files"
- Launch and detail modals widened to 800px to prevent truncation

## v0.5.0

Notebook file lifecycle and environment build versioning.
This release introduces a complete file lifecycle for notebook and SSH sessions, fixing GCS storage mounting and adding structured input/output management with full provenance tracking.

### GCS Storage Fixes

- Fix GCS bucket mounting for notebook and SSH sessions: working bucket config, FUSE CSI annotation, SA key secret mount, and gcloud auth activation for Workload Identity environments
- Fix Workload Identity annotation not applied after namespace was cached
- Add gcs-sync sidecar container for reliable output persistence at shutdown

### Notebook File Lifecycle (ADR-040)

- Input files now mount with directory structure preserved: `/data/{project}/{experiment}/{sample}/{tool}/filename`
- Designated `/outputs/` directory on all session types (Jupyter, RStudio, SSH) for persistent analysis outputs
- On shutdown: outputs synced to GCS, notebook/script files (.ipynb, .Rmd, .R, .py) captured automatically
- Two-phase output persistence: working bucket during session, moved to results bucket on close
- Output files registered with full provenance (source_type=notebook_output, linked to project/experiment)
- 30-minute shutdown timeout for large file sync with UI status indicator
- Fix FILE_INVENTORY.md shell escaping that broke init container file copying partway through

### Environment Build Versioning (ADR-041)

- Rebuilding an environment version creates a minor version (v1 rebuild produces v1.1) instead of overwriting the image
- New `build_number` column on EnvironmentVersion with unique constraint
- Image tags use `v{version}.{build}` format (e.g., `v1.1`, `v1.2`)
- New rebuild endpoint: `POST /environments/{id}/versions/{vid}/rebuild`
- Notebook sessions now link to `environment_version_id` for traceability

### Provenance

- Session provenance endpoint: `GET /notebooks/sessions/{id}/provenance`
- Provenance reports for notebook outputs now include environment version, input files, session resources, and git info
- Markdown and PDF renderers display full source section for notebook and pipeline outputs
- Provenance preview panel displays inline source details instead of skipping nested data

### Frontend

- Shutdown sync indicator: spinner with "Syncing outputs to GCS..." while session stops
- Environment version picker shows `v{version}.{build}` format and passes `environment_version_id` in launch request
- Session detail modal shows environment version and provenance for stopped sessions
- Toggleable quick start guide on Notebooks and Work Nodes pages explaining `/data/`, `/outputs/`, environments, git, and credentials

### Schema Changes

- Migration 057: adds `build_number` to `environment_versions`, `gcs_output_prefix` to `compute_sessions`

## v0.4.1

Fix cellxgene adapter, image pipeline, and publish UX (#195)

## v0.4.0

Usability: real backups, service health, version checking (#194)
