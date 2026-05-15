### Agent Review (LLM integration v1)

- New Settings > Integrations > LLMs page lets an admin configure up to four
  LLM providers (OpenAI, Anthropic Claude, Google Gemini, self-hosted Gemma 4)
  and pick exactly one active at a time. API keys are encrypted at rest;
  hosted-provider activation triggers a data-egress warning.
- New "Agent Review" tab on Pipeline Run and Experiment detail pages.
  Two buttons on Pipeline Run, "Review this pipeline run" and "Review across
  experiment," dispatch async jobs that return severity-coded advisory cards
  (red, orange, green) with a free-text body. Cards filter by active,
  dismissed, stale, or failed and are dismissable org-wide.
- LLM output is advisory only: it never enters provenance or any submission
  artifact. Every invocation writes an audit row with provider, model, the
  last 5 characters of the API key, and the GCS paths of the transmitted
  `.md` artifact, so the org can answer "did we ever send sample X to an LLM"
  with a single SQL query.
- New permissions `llm_integration:configure` (admin) and
  `llm_integration:use` (admin and comp_bio at bootstrap); migration 081
  backfills both into every existing org's system roles.

See ADRs [052](decisions/ADR-052-llm-integration-trust-boundary.md),
[053](decisions/ADR-053-llm-provider-abstraction.md),
[054](decisions/ADR-054-gemma-per-request-inference.md), and
[055](decisions/ADR-055-agent-review-advisory-entity.md).
