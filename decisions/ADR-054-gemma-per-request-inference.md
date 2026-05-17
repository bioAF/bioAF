# ADR-054: Self-hosted Gemma 4 as a Per-Request GCE Inference Pipeline

**Status:** Accepted
**Date:** 2026-05-15
**Deciders:** Brent (repository owner)

---

## Context

Per [ADR-052](ADR-052-llm-integration-trust-boundary.md), bioAF offers a self-hosted LLM path for orgs that will not send pipeline data to third-party providers. The path needs hardware (an L4 or comparable GPU), a model container, and a place to write inference outputs. bioAF already runs Nextflow on GKE for genomics workloads ([ADR-021](ADR-021-kubernetes-compute-backend.md)) and has a work-nodes / GCE migration pattern ([ADR-043](ADR-043-work-nodes-gce-migration.md)) for workloads that benefit from dedicated VMs. The question is whether to stand up a parallel inference service (long-lived endpoint or warm pool) or reuse the pipeline orchestration for a per-request job.

---

## Decision

Gemma 4 inference runs as a per-request job dispatched through the existing pipeline orchestration, provisioning an L4 GPU GCE instance on demand and tearing it down at completion. No warm pool in v1.

### Lifecycle

1. The comp_bio user clicks Button A or Button B; the API process records an `agent_review_job` row and dispatches `execute_gemma(job_id)`.
2. `execute_gemma` builds the `.md` artifact(s) (same code path as the hosted providers), writes them to GCS under the pipeline run's `agent_review_inputs/` subdirectory, and submits a Gemma inference pipeline run to the orchestrator.
3. The orchestrator runs the inference container on an L4 GPU GCE instance: image pull, model load, prompt inference, write the response text to GCS.
4. The instance is torn down by the orchestrator when the pipeline reaches a terminal state.
5. The pipeline monitor detects the terminal state and invokes `agent_review_job_service.on_gemma_complete(job_id, success, output_gcs_path)`. The hook reads the output from GCS, parses it (same parser as hosted), writes the `agent_review` row, transitions the job, and writes the audit row.

To the orchestrator, a Gemma review job is just a small custom pipeline: provision instance, pull image, load model, run inference, write output, tear down. It reuses the spot/on-demand decision tree, the cost-accounting hook, and the failure-mode taxonomy already in place for genomics pipelines.

### Why per-request, not warm pool

Cold start (image pull, model load on L4) can take several minutes. The user accepts this latency because the entire feature is async: clicking the button immediately renders a pending card, and the scientist resumes work. A warm pool would idle a GPU at non-trivial hourly cost ($~0.70/hr for an L4 instance on GCE at current pricing) for an unbounded fraction of the day; v1 buys "no idle cost" at the price of "cold-start latency." A future revision can add a warm pool without breaking the contract.

### Resource-constraint failures

GCP quota exhaustion, regional capacity, image-pull failure, or any other provisioning error surfaces to the user as an explicit failed card with text like "we cannot launch Gemma right now due to resource constraints in GCP, please try again later." There is no auto-fall-back to a hosted provider: the org that selected Gemma did so for a data-egress reason, and silently failing over would violate that contract. The user sees the error and decides.

### Budget

Gemma usage falls under the existing GCP project budget. The existing budget-enforcement plumbing applies; if the org has set a hard budget cap, Gemma jobs back off when the budget is reached, the same as any other pipeline.

### Data containment

The `.md` artifacts go to GCS in the bioAF project. The container reads them from GCS, runs locally on the instance, and writes its response back to GCS in the same project. No data leaves the project under any code path. The `agent_review` row, the audit row, and the artifact paths all live in the bioAF database, which is also in the project.

### Model variant

The default Gemma variant is the smallest that meets the quality bar (fits on L4), locked at deployment time and configurable per release via the fallback model list (`app/services/llm_provider_models.py`). Larger variants requiring H100 are not supported in v1.

### Model weights

Open at first release: baked into the container image (faster start, larger image) or downloaded from a GCS bucket at startup (smaller image, slower start). v1 plan is baked; the trade-off is revisited if image size becomes a deployment burden.

### Retry on preemption

Open at first release: whether Gemma jobs use spot instances (cheap but preemptible, retry per [ADR-042](ADR-042-spot-preemption-retry-strategy.md)) or on-demand only (more expensive but no preemption-retry cost). v1 plan is on-demand only; cold-start cost dominates the per-job spend, and preemption retries would multiply it.

---

## Out of scope

- Warm pool, persistent inference service, or pre-provisioned instance.
- Multi-tenant inference sharing one GPU across orgs. Each request is single-tenant by construction.
- H100 or larger GPU SKUs.
- Streaming response. v1 awaits the full response.
- Per-org budget caps separate from the existing GCP project budget.

---

## Consequences

### Positive

- One orchestration layer, not two. Operators already know how Nextflow on GKE behaves; Gemma inherits the failure taxonomy, the cost-accounting hook, and the spot/on-demand decision tree.
- No idle GPU cost. An org that runs five reviews a month pays for five inference jobs, not a 24/7 endpoint.
- Resource-constraint failures surface naturally because they already do for genomics pipelines. The user-facing error language is the same shape.

### Negative

- Cold-start latency is on the order of minutes, not seconds. Acceptable because the entire feature is async, but it does mean Gemma is a worse "interactive" experience than a hosted provider. Documented in the data-egress warning so the admin can choose.
- A long-running orchestrator outage means Gemma reviews never complete. The pipeline monitor is the existing path; the operational characteristics are no worse than for genomics. A soft reaper (move `submitted` Gemma jobs to `failed` after N hours) is a near-future improvement, listed in `spec-llm-integration-jobs.md` as an open question.

---

## References

- [ADR-021](ADR-021-kubernetes-compute-backend.md) -- compute backend.
- [ADR-042](ADR-042-spot-preemption-retry-strategy.md) -- retry semantics for preemption.
- [ADR-043](ADR-043-work-nodes-gce-migration.md) -- work-nodes / GCE migration pattern; Gemma inference is a specialized instance.
- [ADR-044](ADR-044-custom-pipelines.md) -- Gemma review is shaped like a custom pipeline.
- [ADR-052](ADR-052-llm-integration-trust-boundary.md) -- parent trust-boundary ADR.
- [ADR-053](ADR-053-llm-provider-abstraction.md) -- provider abstraction.
- [ADR-055](ADR-055-agent-review-advisory-entity.md) -- `agent_review` entity.
- Specs: `local/spec-llm-integration-jobs.md`.
