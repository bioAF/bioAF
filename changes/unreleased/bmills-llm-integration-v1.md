### Agent Review (LLM integration v1)

- New **Settings > Integrations > LLMs** page lets an admin configure
  one or more hosted LLM providers (OpenAI, Anthropic Claude, Google
  Gemini) and pick exactly one active at a time. API keys are
  encrypted at rest; activating a hosted provider triggers a
  data-egress warning. A self-hosted Gemma 4 backend is in the
  product but its per-request inference path is not yet wired up, so
  the option is hidden from the UI in this release.
- New **AI Review** tab on Pipeline Run and Experiment detail pages.
  Two buttons on Pipeline Run, "Review this pipeline run" and "Review
  across experiment," dispatch async jobs that return severity-coded
  advisory cards (red, orange, green) with a free-text body and a
  prompt-details disclosure. Each tab shows only its own scope:
  pipeline_run reviews live on the run page, experiment reviews live
  on the experiment page. Cards filter by active, dismissed, stale,
  or failed and are dismissable org-wide.
- **Section-builder prompt assembly.** The review modal lets the user
  toggle individual prompt sections (QC, metadata, biological signal,
  cross-sample, interpretation) before running. Defaults are scoped
  per review type. The assembled prompt is previewable via "Display
  prompt," editable for a single one-off run, or saveable as a named
  custom prompt for reuse.
- **Experiment review payload.** Cross-experiment reviews now ship
  the experiment metadata (name, design type, hypothesis, protocol
  version, design variables) plus every sample on the experiment in
  a wide samples table, along with the per-run artifacts. The
  selector includes a master "select all" checkbox for both the runs
  and the optional HTML-report attachments.
- **Audit and egress.** LLM output is advisory only: it never enters
  provenance or any submission artifact. Every invocation writes an
  audit row with provider, model, the last 5 characters of the API
  key, and the GCS paths of the transmitted `.md` artifact, so the
  org can answer "did we ever send sample X to an LLM" with a single
  SQL query.
- **RBAC.** New permissions `llm_integration:configure` (admin) and
  `llm_integration:use` (admin and comp_bio at bootstrap); migration
  081 backfills both into every existing org's system roles.

See ADRs [052](decisions/ADR-052-llm-integration-trust-boundary.md),
[053](decisions/ADR-053-llm-provider-abstraction.md),
[054](decisions/ADR-054-gemma-per-request-inference.md), and
[055](decisions/ADR-055-agent-review-advisory-entity.md).
