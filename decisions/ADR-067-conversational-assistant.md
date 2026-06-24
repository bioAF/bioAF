# ADR-067: Conversational Assistant (action-taking agent over a tool layer)

**Status:** Proposed (drafted alongside the Phase 1 build; promote to Accepted when the loop + API land)
**Date:** 2026-06-24
**Deciders:** Brent (repository owner)

---

## Context

bioAF should be usable by someone who knows their biology and their samples but cannot
author a Nextflow run. The conversational assistant closes that gap: a user describes what
they have and what they want, and an LLM identifies, sets up, and runs the right pipeline on
their behalf, then explains the results.

This crosses the line ADR-052 deliberately drew. ADR-052 made LLM features **advisory only**:
the model's output is a sticky note next to an entity, never an action on the system. The
assistant **takes actions** (it can install pipelines, configure runs, and spend compute on
GKE). That is exactly what ADR-052 placed out of scope ("Free-form user prompts. Future,
possibly never"). So this ADR is the controlled extension of that boundary, and it only holds
if the safety properties live in the system rather than in the model behaving well.

The relevant infrastructure already exists and is reused: RBAC (`require_permission` /
`role_service.has_permission`), the immutable audit log (ADR-009), the provider abstraction
with a single active provider per org (ADR-053), and budget gating in the launch path. What is
net-new is the means to let an LLM drive those: a tool catalog, an enforcement wrapper, an
agentic loop, conversation state, and a plan-then-confirm gate. The current provider clients
are single-shot text-in/text-out with no function calling.

Code-ready scope is in `local/ai_pipeline_run/spec-04-phase1-buildplan.md`; the load-bearing
safety doc is `spec-03-guardrails.md`.

---

## Decision

**Governing principle: tools enforce, the LLM proposes.** The LLM never executes anything
directly. It emits tool calls. Every call passes through one enforcement wrapper (T2), the
single choke point where the safety properties are applied. If the wrapper rejects a call, it
does not happen, regardless of what the model intended.

### The agent acts as the user

The agent has no permissions of its own. It acts as the authenticated user, bounded by their
RBAC role. Two gates apply:

- **`assistant:use`** (new permission) gates *starting* a conversation. Granted to admin,
  comp_bio, and bench; not viewer (a read-only role does not drive an action-taking agent).
- **Per-tool resource permission.** Every tool declares the underlying RBAC permission its
  action requires, checked server-side against the user's role on every call (e.g. `launch_run`
  requires `pipelines:launch`, mirroring the real `POST /api/pipeline-runs` guard). The model's
  claims about what it may do are never trusted.

### Consequence classes and plan-then-confirm

Every tool declares a consequence class:

| Class | Examples | Behavior in the wrapper |
|---|---|---|
| read_only | recommend_pipeline, list_* | execute and return |
| mutating | install pipeline, create entities | execute; confirm if non-trivial (rule TBD when these land) |
| spend | launch_run | NEVER execute on the model's say-so: create an ActionPlan and wait for explicit confirmation |

For spend (and other consequential actions), the agent assembles a plan, the user confirms,
and only then does it execute. **v1 stops at the confirmed plan**: it produces a fully-formed
launch request and does not POST it, honoring the no-run constraint on the demo. The budget
ceiling in the existing launch path remains the hard backstop; the confirm gate is the human
complement.

### Provider is user-selected, tool-calling added inside the abstraction

The provider is the org's active provider via the ADR-053 seam (`get_active` + `get_client`),
never hardcoded. The abstraction gains a `SUPPORTS_TOOLS` capability flag and a
`submit_with_tools(...)` entrypoint, implemented natively per provider (anthropic / openai /
google). Gemma is not tool-capable in v1, so when the active provider is not tool-capable the
assistant is unavailable with a clear message (mirrors `agent_reviews/availability`). A
JSON-protocol fallback for non-native providers is deferred.

### Persistence and audit

Four org-scoped, audited entities back a conversation: AssistantConversation, AssistantMessage,
AssistantToolInvocation (the unit the wrapper acts on; carries consequence_class and the
proposed -> awaiting_confirmation -> confirmed -> executed state), and AssistantActionPlan
(surfaced at the confirm gate). Every attempt against a real tool is written to the audit log,
attributed to the user and marked via the assistant, linked to the invocation, so the chain
intent -> plan -> confirmation -> tool call -> result is reconstructable.

### recommend_pipeline is a shared, deterministic service

Pipeline recommendation is rule-based (assay + organism -> pipeline + reference), not LLM
ranking, so it is deterministic and auditable. It is a bioAF service owned by neither project
and is also consumed by lit_validation's deterministic orchestration.

### Execution shape (v1)

Synchronous: a user turn runs the loop to its next stop (a final answer or a plan awaiting
confirmation) within the request. No streaming, no async job. Loop step cap = 10 tool calls per
turn as the runaway backstop. v1 is backend + tests only; the chat UI is a later slice.

---

## Out of scope (v1)

- Actually executing a launch (flip on later / in a dev env); v1 stops at the confirmed plan.
- JSON-protocol tool-calling fallback for non-native providers (Gemma stays gated off).
- Multi-step workflows and saved presets (lit_validation as a preset is a later phase).
- The broader tool catalog beyond recommend_pipeline + launch_run (list_*, import_by_accession,
  install, check_status, get_metrics, explain_results) and chat UI polish.
- Silent autonomous spend. Every consequential action is confirmed.

---

## Consequences

### Positive

- The safety story is mostly reuse: permission, audit, and budget already exist and exactly
  bound an agent acting as the user. The new, security-critical surface is one wrapper, which is
  small and testable, and is where every guarantee is enforced.
- The agent can misinterpret intent but cannot spend without a validated, confirmed tool call,
  act beyond the user's role, or take an unaudited action. Those properties do not depend on the
  model behaving.
- recommend_pipeline being deterministic keeps the highest-frequency decision out of the model's
  hands and reusable by a deterministic caller.

### Negative

- The agentic loop and per-provider tool-calling are genuinely net-new (the provider clients are
  single-shot today). Real-world tool-use accuracy on the org's actual provider, including the
  weaker self-hosted Gemma, is still unproven beyond a strong-model spike; the wrapper is the
  structural guarantee regardless of model cooperation.
- Plan-then-confirm bounds the misfire surface but does not eliminate it: a non-expert can confirm
  the wrong plan. The plan renders resolved entities in plain language precisely so they can catch
  "not that sample" before spend.
- Tool results (and any paper/file/dataset the agent reads) are untrusted input and must not be
  able to escalate permissions or be followed as instructions; prompt-injection defense on ingested
  data is required as the catalog grows.
- There is no first-class assay field; recommend_pipeline infers assay from free-text sample
  columns (tracked in `local/ai_pipeline_run/TODOS.md`). Misclassification is caught by the confirm
  gate, but an explicit field would be more robust.

---

## References

- [ADR-052](ADR-052-llm-integration-trust-boundary.md) -- parent trust boundary; this ADR extends
  it from advisory-only to action-taking, under the "tools enforce" principle.
- [ADR-053](ADR-053-llm-provider-abstraction.md) -- provider abstraction and single-active config;
  extended here with `SUPPORTS_TOOLS` + `submit_with_tools`.
- [ADR-009](ADR-009-immutable-audit-log.md) -- every tool invocation is audited, via the assistant.
- [ADR-032](ADR-032-custom-rbac.md) -- RBAC; the new `assistant:use` permission and per-tool
  resource checks.
- [ADR-055](ADR-055-agent-review-advisory-entity.md) -- the existing single-pass advisory job; the
  closest precedent for provider use (`get_active` + `submit_override` test seam).
- Specs: `local/ai_pipeline_run/` (spec-00..04, the load-bearing `spec-03-guardrails.md`).
