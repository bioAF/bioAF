# ADR-055: Agent Review as an Advisory Entity

**Status:** Accepted
**Date:** 2026-05-15
**Deciders:** Brent (repository owner)

---

## Context

Per [ADR-052](ADR-052-llm-integration-trust-boundary.md), LLM output is advisory and not provenance. That stance has to be expressed in the data model: the entity that holds a review must be reachable from the UI on the pipeline run or experiment it pertains to, but must not be reachable from any provenance graph, lineage join, or submission-artifact rollup. The lifecycle also has to support: pending UI placeholders, success and failure as first-class states, parse-failure as a flavor of success, org-wide dismissal that is reversible, staleness for experiment-level reviews when the experiment grows, and concurrency rules that prevent the same user from accidentally double-spending against the active provider.

---

## Decision

Introduce `agent_review` (the user-facing record) and `agent_review_job` (the operational record), as two additive tables. The job is the lifecycle owner of the work; the review is the surface the user sees. Every review (success or failure) is a unique row; re-running creates a new row; nothing is ever overwritten.

### `agent_review_job` (operational)

One row per dispatched job. Columns and indexes are defined in `spec-llm-integration-jobs.md`. The relevant invariants:

- Status machine: `pending -> building_artifacts -> submitted -> {succeeded, failed}`. A succeeded job with a parse-failure response sets a `parse_failure` detail in the audit row but the status stays `succeeded`.
- A partial unique index on `(entity_type, entity_id, review_type) WHERE status IN ('pending', 'building_artifacts', 'submitted')` enforces the debounce: at most one in-flight job per `(entity, review_type)`.
- Hosted-path execution is a FastAPI `BackgroundTasks` async task inside the API process. Gemma-path execution submits to the pipeline orchestrator and stores the resulting `pipeline_run_id`; the pipeline monitor wakes the job back up at terminal state.
- On API process restart, in-flight hosted jobs are transitioned to `failed` with reason `process_restart`. Gemma jobs are owned by the orchestrator and are not touched.
- The provider, model, and prompt template version are snapshot onto the row at submission so later changes do not retroactively rewrite history.

### `agent_review` (user-facing)

One row per review. Columns:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGSERIAL` PK | |
| `organization_id` | `INTEGER` FK | Indexed. |
| `triggered_by_user_id` | `INTEGER` FK | |
| `entity_type` | `VARCHAR(32)` | `pipeline_run` or `experiment`. |
| `entity_id` | `BIGINT` | |
| `included_run_ids` | `JSONB` NULL | Experiment-level reviews; array of pipeline_run ids. |
| `review_type` | `VARCHAR(64)` | Matches the prompt template name. |
| `provider`, `model`, `prompt_template_version` | snapshots | Same values as the job row. |
| `status` | `VARCHAR(32)` | `pending`, `succeeded`, `failed`. |
| `severity` | `VARCHAR(16)` NULL | `red`, `orange`, `green`, `unknown`. |
| `headline` | `TEXT` NULL | |
| `flags`, `evidence` | `JSONB` NULL | Parsed from the response header. |
| `body` | `TEXT` NULL | Free-text body or the full raw response on parse failure. |
| `error_text` | `TEXT` NULL | |
| `artifact_gcs_paths` | `JSONB NOT NULL DEFAULT '[]'` | |
| `agent_review_job_id` | `BIGINT` FK UNIQUE | One-to-one with the job. |
| `dismissed_at`, `dismissed_by_user_id` | NULL | |
| `created_at`, `completed_at` | | |

Indexes:

- `(organization_id, entity_type, entity_id, created_at DESC)` for the Agent Review tab query.
- `(agent_review_job_id)` UNIQUE.
- `(organization_id, status)`.
- `(organization_id, dismissed_at)` for the dismissed filter.

### No reference from provenance

`agent_review` is not referenced from `pipeline_run`, `experiment`, `sample`, `analysis_snapshot`, or any lineage join table. The relationship is the other way: an `agent_review` row points to its entity, but the entity does not know an `agent_review` exists. The Agent Review tab queries by `(entity_type, entity_id)`. This makes "the LLM note is advisory" a structural property: dropping `agent_review` entirely would not change any lineage, any provenance report, or any submission rollup.

### Pending card placeholder

When the user clicks a button, the endpoint writes an `agent_review` row with `status = pending` and returns its id alongside the job id. The UI optimistically renders that pending card in the Agent Review tab. The same row transitions to `succeeded` or `failed` when the job terminates; the UI does not create a second row.

### Dismissal

Org-wide. One user dismisses, all users see it as dismissed. Reversible via the dismissed-card modal. v1 does not write a separate audit row for dismissal: the `dismissed_at`/`dismissed_by_user_id` columns are the record. If compliance reviewers later ask for a per-action audit trail of dismissals, that becomes an additive change.

### Staleness

Computed at query time, not persisted as a column. A review is stale when `entity_type = 'experiment'` and the experiment currently contains a pipeline run that is not in `included_run_ids`. Adding a sample to the experiment without adding a pipeline run does not change `stale`. Single-run reviews are never stale (pipeline runs are immutable in bioAF). The flag is materialized in the API response per row at query time.

### Concurrency

The partial unique index on the job table is the source of truth. The service layer catches the unique-violation, queries the existing in-flight job, and raises `JobAlreadyRunning(existing_job_id)`. The endpoint returns 409 with the existing job id and review id so the UI can navigate to (or surface) the existing pending card instead. A user can run Button A on Pipeline Run X concurrently with Button B on Experiment E that includes Run X: the entity differs, so the index does not collide.

---

## Out of scope

- Editing or annotating a review card. The card is what the LLM said; it is not user-edited content.
- Threading or follow-ups inside a card. Re-running creates a new card, not a thread.
- Tagging, categorizing, or full-text searching reviews.
- Per-user dismissed state.
- Notifications when a review completes. v1 is "user comes back to the tab and refreshes."
- "Refresh" button on a stale card. v1 is manual re-run.
- Project-level reviews. Out of scope per ADR-052.

---

## Consequences

### Positive

- The advisory-vs-provenance distinction is structural. A future contributor who wanted to feed LLM output into a lineage graph would have to add a new FK; the existing schema does not invite the mistake.
- Two-table split (operational job, user-facing review) keeps the UI query path simple (one row per card) without losing operational detail (artifact paths, error class, debounce key) on the job side.
- Debounce by partial unique index is enforced at the DB layer; race-condition bugs in the application code cannot create double-submission.

### Negative

- Two tables instead of one. Acceptable because the lifecycles really are different: a job is short-lived operational state, a review is a long-lived user-facing record. Collapsing them would push operational columns onto the user-facing row and make the API response noisier.
- Staleness at query time means the API response is technically non-deterministic across reloads (the same row can flip to `stale=true` between two requests). Acceptable; this is the intended UX (the badge appearing is the signal).
- Org-wide dismiss is a slight surprise for users who expect personal dismissal. Documented in the UI; if scientists complain, per-user dismissal is additive.

---

## References

- [ADR-009](ADR-009-immutable-audit-log.md) -- audit-row pattern; the addendum lists the new actions.
- [ADR-019](ADR-019-pipeline-review-handoff.md) -- review hand-off is a different concept; cross-reference for clarity.
- [ADR-052](ADR-052-llm-integration-trust-boundary.md) -- parent trust-boundary ADR.
- [ADR-053](ADR-053-llm-provider-abstraction.md) -- provider abstraction and response contract.
- [ADR-054](ADR-054-gemma-per-request-inference.md) -- Gemma execution path.
- Specs: `local/spec-llm-integration-jobs.md`, `local/spec-llm-integration-ui.md`.
