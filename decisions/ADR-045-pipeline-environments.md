# ADR-045: Pipeline Environments

**Status:** Accepted
**Date:** 2026-04-26
**Deciders:** Brent (repository owner)

---

## Context

bioAF has two environment types: `"notebook"` (Docker images for RStudio/Jupyter on K8s) and `"work_node"` (Packer VM images for GCE). Custom pipelines need container images with the right dependencies, but they do not fit either existing type:

- They run on K8s (like notebooks), not GCE (like work nodes).
- They are managed under the Pipelines menu, not the Workbench menu.
- They share no UI surface with notebook or work node environments.

Reusing the existing `Environment` + `EnvironmentVersion` models is the right approach -- the versioning logic, build system, and Conda YAML handling already work. The question is how to route a third type through the existing infrastructure.

---

## Decision

### Add `"pipeline"` as a Third Environment Type

The `Environment.environment_type` field accepts a new value: `"pipeline"`. This routes through the **Docker build path** (same as notebooks), not the Packer path (work nodes).

### Build Routing

In `environment_build_service.py`:

- `"notebook"` and `"pipeline"` both use the Docker/Cloud Build path: wrap Conda YAML in a Dockerfile with `FROM continuumio/miniconda3:latest`, upload build context to GCS, submit Cloud Build, store image in Artifact Registry.
- `"work_node"` continues to use the Packer/GCE image path.

No new build logic is needed. The only change is adding `"pipeline"` to the condition that selects the Docker path.

### Default Pipeline Environment

On application startup, `ensure_default_pipeline_environment()` creates a base pipeline environment if none exists for the organization. Default Conda spec: Python 3.11 with numpy, pandas, scipy, matplotlib, scikit-learn (same base packages as the default work node environment).

### UI Separation

Pipeline environments are surfaced under **Pipelines >> Environments**, separate from **Workbench >> Environments** (which shows notebook and work node types). Both pages use the same environment API endpoints, filtered by `environment_type`.

### Cascade to Pipeline Versions

When a pipeline environment build completes, the system emits an `ENVIRONMENT_BUILD_COMPLETED` event via the event bus. The `CustomPipelineService` subscribes to this event and creates new pipeline versions for any pipelines using the updated environment. See ADR-046 for details.

---

## Consequences

- Three environment types (`notebook`, `work_node`, `pipeline`) share one model and versioning system, reducing code duplication.
- Pipeline environments use Docker images (not Packer VMs), matching their K8s execution target.
- The Pipelines and Workbench menus each have their own Environments page, filtered by type.
- The environment build service routes on type, with `"pipeline"` and `"notebook"` sharing the Docker path.
