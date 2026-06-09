# ADR-066: TerraformExecutor is the single Terraform execution owner

**Status:** Accepted
**Date:** 2026-06-09
**Deciders:** Brent (repository owner)
**Supersedes:** the implementation in [ADR-007](ADR-007-ui-driven-terraform.md)
(single root config + `enable_<component>` tfvars + synchronous `TerraformService`).
The UI-driven principle of ADR-007 stands; only its execution mechanism is replaced.

## Context

Two Terraform execution paths had accreted:

1. **`TerraformService`** (the ADR-007 design): ran `terraform` in a single root
   configuration (`/app/terraform`) toggled by `enable_<component>` tfvars, committed
   `terraform.tfvars` to a GitOps repo, applied synchronously, and was wired to the
   per-component `/api/components/{key}/enable|disable|configure` endpoints and the
   `/api/terraform` router.
2. **`TerraformExecutor`** (Phase 17): runs each module under
   `backend/terraform/modules/<module>` in its own work dir with per-module GCS state
   (`-backend-config=prefix=<module>`), credential injection, streaming apply, and a
   global single-flight lock. This is what actually deploys real installs
   (`foundation`/`storage`/`compute`/`billing_export`) via `stack_deployment` and
   `/api/v1/infrastructure/terraform`.

The root config that `TerraformService` targeted was never copied into the shipped
backend image (`docker/Dockerfile.backend` copies only `backend/terraform/`, which is
`modules/` alone), so its plan/apply were no-ops in every real install. The
per-component enable/disable/configure feature it backed never deployed anything via
Terraform; the live mechanism is `component_states` + Cloud Build image builds +
on-demand Kubernetes runtime adapters, with components riding on the shared `compute`
module. Two unsynchronized global `_tf_lock`s also existed, one per service.

This left a confusing "who runs Terraform?" with two answers, a dead root config
(top-level `terraform/`) that only CI still validated, and an orphaned per-component
config UI (`ComponentConfigPanel` + a detail route) that drove a no-op plan/confirm.

## Decision

`TerraformExecutor` is the **single** Terraform execution owner. Concretely:

- Removed `TerraformService`, the `/api/terraform` router, the per-component
  `/api/components/{key}/enable|disable|configure` (and `GET /{key}`) endpoints and
  their orphaned `ComponentService` methods, the dead
  `GET /api/v1/infrastructure/components` endpoint, the orphaned config UI
  (`ComponentConfigPanel` + the `/infrastructure/components/[id]` route), and the dead
  top-level `terraform/` root configuration.
- Components are not deployed via Terraform. Per-component enable/disable remains a
  first-class, admin-only UI action via the existing stack toggle
  (`/api/v1/infrastructure/stack/components/{key}/toggle`); it is intentionally not on
  the public `/v1/integrations` API.
- CI's `terraform-validate` job now validates the real shipped modules under
  `backend/terraform/modules` instead of the deleted root config.
- A guard test asserts the Terraform CLI is invoked only from `terraform_executor.py`.

## Rationale

The executor is the engine that actually provisions infrastructure; the root-config
path was dead weight that produced a second lock, a parallel run model, and a
misleading per-component "deploy" surface. Consolidating on one owner removes the
ambiguity and the unsynchronized-lock hazard, and makes CI validate what ships.

This is a code-removal change with no migration: it touches no live infrastructure,
makes no schema changes (legacy `terraform_runs.component_key`/`plan_summary_json`
columns are left for history), and preserves the UI behavior users rely on.

## Consequences

**Positive:** one Terraform engine, one lock, one state model (per-module GCS
prefixes); CI validates shipped terraform; the orphaned per-component config theater
is gone; the codebase no longer implies components deploy via Terraform.

**Negative / honest limits:** `TERRAFORM_APPLY_FAILURE` (the "Deployment failed"
notification) lost its only emitter when `TerraformService` was removed; it never
fired in shipped installs anyway because the executor never emitted it. Wiring the
executor's apply-failure path to emit it is tracked as a follow-up.

**Neutral:** the `config_schema` field remains on catalog entries (now consumed only
by a catalog-hygiene test); removing it is a separate cleanup.

## References

- [ADR-007](ADR-007-ui-driven-terraform.md) (UI-driven Terraform; principle retained,
  implementation superseded here)
- `backend/app/services/terraform_executor.py`, `backend/app/services/stack_deployment.py`
- `backend/terraform/modules/{foundation,storage,compute,billing_export}`
