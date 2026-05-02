# ADR-044: Custom Pipelines

**Status:** Accepted
**Date:** 2026-04-26
**Deciders:** Brent (repository owner)

---

## Context

bioAF's pipeline system supports NF-Core and Nextflow workflows. These are powerful for standardized bioinformatics pipelines but impose overhead for labs that just need to run existing scripts (bash, Python, Perl, R) against tracked data with provenance.

Many labs operate this way for years -- a Python script that pulls a model from GCS and runs inference on data files, a bash pipeline that preprocesses and aligns reads, a Perl script from a legacy codebase. These scripts are reusable, shared within the org, and need the same provenance guarantees as NF-Core pipelines: what went in, how was it processed, what came out.

The existing pipeline infrastructure (K8s Job submission, output collection, log persistence, OOM detection, status monitoring) is generic enough to support non-Nextflow workloads. The gap is in pipeline definition and versioning -- there is no way to define "run this command with this code in this environment" and version that definition for reproducibility.

---

## Decision

### Custom Pipeline as a Versioned, Catalog-Integrated Entity

A custom pipeline is a named, org-scoped, reusable definition that appears in the pipeline catalog alongside NF-Core pipelines. It consists of:

1. **Identity:** Name, description, creator, organization.
2. **Versions:** Each version is an immutable snapshot of what the pipeline does: code source, entrypoint command, environment image, resource requirements, variables, and optional log/report configuration.
3. **Catalog entry:** A `PipelineCatalogEntry` with `source_type="custom"` provides discoverability through the existing catalog API.

### Data Model

Two new tables:

**`custom_pipelines`** -- the parent entity:

- `id`, `organization_id`, `name`, `description`, `pipeline_key` (unique per org), `created_by_user_id`, timestamps.

**`custom_pipeline_versions`** -- versioned definitions:

- `custom_pipeline_id`, `version_number` (auto-incremented per pipeline).
- Code source: `code_source_type` ("github_repo", "code_blob", "inline"), `github_repo_id` (FK to existing `github_repos`), `code_content` (for blob/inline).
- Execution: `entrypoint_command`, `environment_version_id` (FK to `environment_versions`).
- Resources: `cpu_request`, `memory_request`.
- Logging: `log_file_path` (optional, overrides pod stdout/stderr as primary log source post-completion).
- Metadata: `version_trigger` ("user" or "environment_cascade"), `status` ("active" or "deprecated"), `created_by_user_id`.

**`custom_pipeline_variables`** -- variable definitions per version (see ADR-046).

Modified tables:

- `pipeline_catalog`: Add `custom_pipeline_id` FK (nullable). One-directional -- catalog points to pipeline.
- `pipeline_runs`: Add `custom_pipeline_version_id` FK (nullable). Provenance link to exact version that produced the outputs.

### Execution Model

Custom pipelines run as K8s Jobs on the existing pipeline node pool, reusing:

- **Input staging:** GCS-to-`/data/` via init containers (same as NF-Core).
- **Output sync:** `trap EXIT` wrapper uploads `/outputs/` to GCS results bucket.
- **Monitoring:** 30-second polling loop picks up any `PipelineRun` with `k8s_job_name`.
- **OOM detection:** `_classify_failure()` checks K8s `OOMKilled` termination reason.
- **Log persistence:** `persist_job_logs()` saves pod stdout/stderr to GCS on completion.

New behaviors for custom pipelines:

- **Working directory** set via K8s `workingDir` based on code source type: `/code/{repo_name}` for github_repo, `/code` for code_blob, `/data` for inline.
- **Report detection:** Checks for `/outputs/report/report.html` (self-contained) or `report.md` (server-rendered) in collected outputs.
- **Custom log file:** If `log_file_path` is set, that file from `/outputs/` becomes the primary log view post-completion.
- **Skip Nextflow-specific logic:** No trace.tsv parsing, no Nextflow report registration, no sample sheet generation.

### Catalog Integration

Custom pipelines are first-class catalog entries. The `GET /api/pipelines` endpoint returns them alongside NF-Core pipelines. The response includes `created_by_username` for custom entries. The frontend renders differently based on `source_type`: NF-Core entries show a source badge and Nextflow version; custom entries show the creator username and latest pipeline version number.

---

## Consequences

- Labs with existing scripts can use bioAF's pipeline system without learning Nextflow.
- Full provenance tracking: pipeline version, environment version, input files, variable values, output files.
- Custom pipelines share infrastructure (K8s, GCS, monitoring, RBAC) with NF-Core pipelines, reducing maintenance burden.
- The catalog becomes polymorphic (NF-Core and custom), requiring type-aware frontend rendering.
- Pipeline version history may include both user-created and environment-cascade versions, which need clear UI treatment.
