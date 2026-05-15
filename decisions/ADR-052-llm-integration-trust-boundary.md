# ADR-052: LLM Integration Trust Boundary and Advisory Output Model

**Status:** Accepted
**Date:** 2026-05-15
**Deciders:** Brent (repository owner)

---

## Context

Lab scientists running bioAF want an advisory layer that scans completed pipeline outputs and across-experiment comparisons for patterns or concerns, then surfaces those as flags they can act on. Today bioAF has no LLM-facing surface at all: no provider abstraction, no payload contract, no audit vocabulary for inference calls, no in-app review UI.

The feature has to land without compromising two existing invariants:

1. **Provenance integrity.** The scientific record (samples, pipeline runs, lineage, submission artifacts) is the system's core deliverable. Anything an LLM produces must not be confused with, or referenced from, that record.
2. **Explicit data egress.** Some orgs will not send pipeline data to third-party providers. Any LLM feature must default-deny external transmission until an admin explicitly opts in, and must also offer a self-hosted path for orgs that will never opt in.

This ADR is the parent contract under which every other LLM-related decision sits: provider abstraction (ADR-053), self-hosted Gemma 4 execution (ADR-054), the agent_review entity model (ADR-055), and addenda to ADR-009 (audit log), ADR-032 (RBAC), and ADR-047 (encryption).

---

## Decision

LLM features in bioAF are **advisory only**. The output of an LLM review is a research-assistant sticky note that lives next to the entity it pertains to. It is not provenance, not part of pipeline lineage, not part of submission artifacts, and not user-edited content.

### Trust postures

Two postures are supported simultaneously:

- **Hosted.** OpenAI, Anthropic Claude, Google Gemini. Data leaves the org to a third-party provider. Gated by admin opt-in plus an explicit data-egress warning at enable time.
- **Self-hosted.** Gemma 4 running inside the bioAF GCP project (ADR-054). No data egress beyond the deployment's own project.

Exactly one provider is active at a time per org. Multi-key storage with single-active toggle preserves switching speed without complicating prompt construction, model selection, or audit.

### Payload contract

The only data that may leave the org under any LLM code path:

- The standardized `.md` rollup per pipeline run (defined in `spec-llm-integration-payload.md`): pipeline_run record, output JSON, QC report text, sample metadata, errors, pipeline metadata.
- The pipeline's HTML report, only when an opt-in checkbox is set per run.

The following never ship under any code path:

- Raw rows from any table.
- FASTQ files or any file blobs.
- Pipeline stdout/stderr or container logs.

### Standardized prompts only in v1

The user does not type a prompt. Two buttons run two versioned templates (`pipeline_run_review_v1`, `experiment_run_comparison_v1`). This bounds the data-egress surface (we know exactly what we ship), keeps the audit log meaningful, and avoids prompt-engineering responsibility leaking onto scientists.

### Default deny

Until an admin enables a provider, no LLM call can be made. The review buttons are hidden on every page. The Agent Review tab still renders (read-only history of any prior reviews), but the entry points are gone.

### Audit

Every invocation writes audit rows (`llm_review_submitted`, `llm_review_succeeded`, `llm_review_failed`), every config change writes one (`llm_provider_enabled`, `llm_provider_disabled`, `llm_provider_key_rotated`). The audit row references the GCS path of the `.md` artifact that was sent, so a compliance reviewer can later answer "did we ever send sample S-123 to an external LLM" with a single SQL query.

---

## Out of scope

- Literature ingestion, RAG over papers, paper comparison against pipeline output. Future.
- Free-form user prompts. Future, possibly never.
- Project-level reviews and isolated single-sample reviews.
- Per-experiment or per-project "LLM-prohibited" tags. v1 is admin-level toggle only.
- Auto-fall-back from Gemma to a hosted provider on Gemma failure. Orgs that picked Gemma did so for a data-egress reason; silent failover violates that contract.
- Cost guardrails on hosted-LLM spend. The org accepts provider billing.

---

## Consequences

### Positive

- Provenance stays clean. Scientific record is unchanged by the presence of LLM notes; downstream tools that consume lineage do not need to know LLM features exist.
- Data-egress decisions are explicit, per-org, admin-only, and irreversible only by the admin who flipped the toggle. The self-hosted path means "we will never send data out" is a configurable answer, not a feature gap.
- Audit is sufficient on its own for compliance review without retaining full payloads in the audit log (the `.md` artifacts persist in GCS).

### Negative

- Two execution paths underneath one job model (hosted HTTP vs. Gemma pipeline) is more code than one. Acceptable: each path is small, and the user-facing contract is identical.
- The "advisory only" claim is documented and enforced by referential structure (no FK from any provenance graph into `agent_review`), not by code-level prohibition. A future contributor could in principle add such a reference; the ADR exists to make that visibly wrong.
- Standardized prompts mean less flexibility for power users. Acceptable for v1; revisit if scientists hit the ceiling.

---

## References

- [ADR-009](ADR-009-immutable-audit-log.md) -- every invocation is audited; addendum names the new actions and `details_json` shape.
- [ADR-019](ADR-019-pipeline-review-handoff.md) -- review hand-off is a different concept; cross-reference for clarity.
- [ADR-047](ADR-047-data-at-rest-encryption.md) -- provider API keys are encrypted credentials.
- [ADR-053](ADR-053-llm-provider-abstraction.md) -- provider abstraction and single-active configuration.
- [ADR-054](ADR-054-gemma-per-request-inference.md) -- self-hosted Gemma 4 execution path.
- [ADR-055](ADR-055-agent-review-advisory-entity.md) -- the `agent_review` entity and lifecycle.
- Specs: `local/spec-llm-integration-overview.md`, `local/spec-llm-integration-audit.md`.
