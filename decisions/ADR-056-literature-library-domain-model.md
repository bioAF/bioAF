# ADR-056: Literature Library Domain Model

**Status:** Accepted
**Date:** 2026-05-18
**Deciders:** Brent (repository owner)

---

## Context

bioAF tracks samples, experiments, projects, pipeline runs, and files,
but it has no first-class concept for the scientific literature a lab
reads, annotates, and reasons over. Today that literature lives in
Slack, email, and personal Zotero libraries. Papers get lost; context
gets lost; useful discussion never accumulates as a queryable record.

A new Literature Library feature has been scoped, but the original
planning documents (drafted in `local/Literature/` on 2026-05-18) used
terminology that conflicts with the bioAF glossary
(`ai_guides/glossary.md`) and with [ADR-055](../decisions/ADR-055-agent-review-advisory-entity.md).
Specifically the original drafts:

- Used "AI Review" and "AI Lit Review" where the project uses "Agent
  Review" (per ADR-055 and the glossary).
- Proposed UUID primary keys; the rest of the codebase uses integer
  (`BIGSERIAL`) PKs.
- Proposed a "AI Lit Review" entity that is shape-incompatible with
  Agent Review (different output type, lifecycle, severity scheme),
  yet sat under the same name.
- Coupled paper annotation to a frontend PDF.js / `react-pdf-highlighter`
  layer that does not exist in the codebase and that would materially
  enlarge v1.

This ADR resolves the domain language and the data-model commitments
for the v1 Literature Library. It is the upstream decision; downstream
specs (`SPEC-literature-data-model.md`, `SPEC-literature-library.md`,
`SPEC-literature-sources-and-search.md`,
`SPEC-literature-lit-review-run.md`, `SPEC-literature-api.md`) conform
to it.

---

## Decision

### New glossary entries (proposed for `ai_guides/glossary.md`)

The following terms are added as level-3 glossary entries. They are
used consistently in the rewritten `local/Literature/` documents.

- **Paper.** A scholarly publication tracked in the
  Literature Library, identified by DOI (primary)
  or normalized title plus first-author and last-author keys (fallback).
  Org-scoped, org-readable; immutable identifier, mutable metadata.
  Not a [Reference Dataset](../decisions/ADR-017-reference-data-management.md)
  (those are curated biological references such as genomes, annotations,
  indexes). May be associated with one or more
  Experiments, Projects, or globally to the
  Organization.

- **Literature Library.** The org-scoped collection of
  Papers, their associations, comments, reading status, and
  dismissals. Distinct from the Reference Dataset
  registry; distinct from the file/document subsystem (papers live in a
  dedicated GCS bucket and a dedicated set of tables).

- **Lit Review Run.** A single execution of an LLM-driven paper
  recommendation job scoped to an Experiment. Uses the
  org's active LLM Provider to generate source queries
  and rank candidates, producing
  Literature Recommendations.
  On demand in v1; scheduled in v2. Not an
  Agent Review: different output shape
  (queue of recommendations vs severity-coded advisory note), different
  lifecycle, different table.

- **Literature Recommendation.** A single LLM-scored paper produced by
  a Lit Review Run, with a continuous relevance
  score (0.0 to 1.0) and one-sentence reasoning. Lifecycle:
  `pending -> accepted` (paper joins library, associated with the
  scope) or `pending -> dismissed` (paper is dismissed org-wide via
  Literature Dismissal).

- **Literature Source.** An external bibliographic data source the
  bioAF Literature Library can query. Four in v1: PubMed (NCBI),
  bioRxiv, Europe PMC, Semantic Scholar. Per-org configuration of
  enablement and API key. API keys stored encrypted at rest via the
  `EncryptedString` pattern from
  [ADR-047](../decisions/ADR-047-data-at-rest-encryption.md).

- **Literature Dismissal.** An org-wide signal that a
  Paper should be excluded from default library views, future
  Lit Review Runs, and any
  Agent Review literature payload. Created by `admin`
  or `comp_bio`. Reversible by `admin` only.

The existing **Agent Review** glossary entry gains a one-line note that
"abstracts and comments from associated Papers may be bundled
into the Agent Review's prompt artifact when the org's Literature
inputs are enabled." See [ADR-057](ADR-057-literature-as-input-to-agent-review.md).

### Lit Review Run is a sibling concept to Agent Review

Lit Review Run is **not** a subtype of `agent_review` and does **not**
share its table. The two entities differ on output shape (queue of
recommendations with continuous relevance scores vs single
severity-coded advisory), lifecycle (`pending|running|complete|partial|failed`
with downstream recommendation lifecycle vs Agent Review's
`pending|building_artifacts|submitted|{succeeded,failed}`), permission
action (`run_lit_review` vs `submit_agent_review`), and scope
(Experiment in v1 vs Pipeline Run / Experiment for Agent Review).

The two features share infrastructure: the org's active LLM Provider
(per [ADR-053](../decisions/ADR-053-llm-provider-abstraction.md)),
the experiment-context portion of
`backend/app/services/agent_review_artifact_builder.py`, the audit log,
and the event bus. They live in separate tables, expose separate API
surfaces, and are documented in separate ADRs.

### Storage in a per-org bucket

Literature artifacts live in a new per-org GCS bucket
`bioaf-literature-{org}` provisioned via a new `google_storage_bucket
"literature"` resource in `backend/terraform/modules/storage/main.tf`.
The bucket inherits the standard bioAF defaults: object versioning on,
delete protection on, standard storage class.

GCS layout:

```text
bioaf-literature-{org}/
  papers/
    {paper_id}/
      original.pdf
      extracted.txt           # full-text extraction for Agent Review
      thumbnail.png           # first-page thumbnail
      pages/
        {n}.png               # lazily rendered page images for the v1 viewer
```

This is not stored in any existing bucket. Lifecycle and access
patterns differ from sequencing data, documents, and reference data.

### Primary keys

All new Literature tables use `BIGSERIAL` integer primary keys, matching
the rest of the codebase
(`experiments.id`, `users.id`, `agent_reviews.id`, etc.).

### Schema commitments

The v1 Literature schema introduces ten tables (full DDL in
`SPEC-literature-data-model.md`):

- `literature_papers`
- `literature_paper_comments`
- `literature_associations`
- `literature_paper_reading_status`
- `literature_paper_dismissals`
- `literature_sources_config`
- `literature_searches`
- `literature_search_results`
- `literature_review_runs`
- `literature_recommendations`

Three tables are explicitly **not** in v1:

- `literature_review_schedules` -- defers until the generic
  `ScheduledJob` primitive lands.
- `literature_saved_searches` -- defers to v2.
- `literature_assets` -- defers to v2; no figure/table extraction in v1.

Paper notes and replies live in a single threaded
`literature_paper_comments` entity (with `parent_id` self-reference and
soft delete). There is no separate "annotation" table in v1. Quote-
anchored highlights are deferred to v2 and will be added as an optional
`anchor_json` column on the same table.

Recommendation relevance is captured as a continuous
`relevance_score FLOAT` (0.0 to 1.0) with a derived `relevance_bucket`
(`high` >= 0.66, `medium` >= 0.33, `low` otherwise). The thresholds are
configurable per Lit Review Run via `score_threshold`. This is
deliberately distinct from Agent Review's red/orange/green severity:
relevance is a score on a paper recommendation; severity is a flag on
an advisory note.

### Permission resource

A single new resource `literature` is registered in
`role_service.ALL_RESOURCES_ACTIONS`
(per [ADR-032](../decisions/ADR-032-custom-rbac.md)) with twelve
sub-actions:

```text
literature: [
  view, upload, comment, associate,
  delete_own_comment, delete_any_comment, delete_paper,
  dismiss, reverse_dismiss,
  run_search, run_lit_review, configure_sources
]
```

Role grants (seeded in `bootstrap_roles.py`):

- `admin`: all actions.
- `comp_bio`: all actions except `reverse_dismiss` and `delete_any_comment`.
- `bench`: `view`, `upload`, `comment`, `associate`, `delete_own_comment`,
  `run_search`.
- `viewer`: `view` only.

### Provenance

Every Paper carries a `provenance` field with one of:

- `user_upload` -- a user uploaded the PDF or registered metadata.
- `source_search` -- added as a result of a user-run ad-hoc search.
- `lit_review_run` -- added when a user accepted a Literature
  Recommendation produced by a Lit Review Run.

Combined with `added_by_user_id` and (through
`literature_recommendations.review_run_id`) the originating Lit Review
Run, the filter UI mirrors the Dataset Browser provenance pattern.

---

## Rationale

**Why a new top-level entity instead of folding into Agent Review?**
Agent Review (ADR-055) is by design a single severity-coded advisory
note attached to a specific Pipeline Run or Experiment. Lit Review Run
produces a *queue* of paper recommendations attached to an Experiment
and is repeated over time. Forcing both into one table requires a
discriminator column, a large nullable field surface
(severity? relevance_score? recommendations array vs single body?),
and continuous "is this row this kind or that kind" branching at every
query site. A clean sibling concept costs one extra ADR and saves all
of that.

**Why integer PKs instead of UUIDs?** Every other table in the
codebase that any of these will join to uses integer PKs
(`experiments.id`, `users.id`, `agent_reviews.id`, etc.). Mixing UUIDs
for Literature would force extra cast logic at every join and break
the visual consistency of `EXPLAIN` plans, audit log entries, and
foreign-key documentation. The cost of an integer PK leak is not
relevant at lab scale.

**Why a per-org bucket instead of a prefix in an existing bucket?**
Per-org bucket isolation makes lifecycle (delete an org, drop a bucket)
and cost attribution clean. The Terraform pattern is already in place
for `ingest`, `raw`, `results`, and `references`; adding `literature`
is one resource block. Sharing the documents bucket would mix
lifecycle and access patterns and would surface in any future
per-bucket retention policy.

**Why a single `literature` permission resource with sub-actions
instead of multiple resources (`literature_papers`,
`literature_comments`)?** Lab-scale orgs do not customize permissions
at the entity level; the four built-in roles cover 95% of usage.
A single resource keeps the seed table small, keeps the audit log
consistent, and is straightforward to extend with new sub-actions as
the feature grows.

**Why score (0.0-1.0) instead of red/orange/green for recommendations?**
Relevance is a quantity (how close is this paper to my experiment?),
not a category. Mapping a model's continuous output to three buckets
at write time would discard information at the moment of capture and
make threshold tuning impossible without a re-run. We *derive* a
bucket at write time for UI grouping, but the score is the data and
the bucket cutoffs are configurable per run.

**Why threaded comments without PDF anchoring in v1?** The frontend
has no PDF rendering infrastructure. Adopting PDF.js plus
`react-pdf-highlighter` is several weeks of frontend work and brings
substantial anchoring complexity (re-anchor on re-upload, coordinate
drift, character-index drift across PDF renderers). v1 ships
paper-scoped comments because they are useful immediately and because
v2 can add an optional `anchor_json` column without breaking the
schema or the API. The cost of deferring is that comments are not
quote-grounded for the LLM in v1; the cost of not deferring is a four-
to-eight-week scope inflation on the riskiest part of the feature.

---

## Consequences

**Easier:**

- The rewritten `local/Literature/` documents are coherent with the
  glossary and with ADR-055. New code in `backend/app/models/` and
  `backend/app/services/literature/` follows the established
  conventions (Integer PKs, `EncryptedString`, audit log via
  `log_action`, permission checks via `require_permission`).
- Lit Review Run extension to scheduled cadences (v2) only requires
  adding `literature_review_schedules` and wiring it to the new
  `ScheduledJob` primitive; the Lit Review Run table itself does not
  change.
- Quote-anchored highlights (v2) only require adding an `anchor_json`
  column on `literature_paper_comments` and a new viewer; existing
  comments remain valid.
- Existing infrastructure (audit log, event bus, notification router,
  LLM Provider clients, encryption, role service, Agent Review
  artifact builder) is reused rather than re-implemented.

**Harder:**

- Two ADRs (ADR-056 and ADR-057) are required before implementation,
  and both must clear `ai_guides/` code-owner approval. The glossary
  entries land at the same time, which means the glossary change goes
  through the same review.
- The per-org bucket adds a Terraform module change. The first org
  that uses Literature must wait for its bucket to provision before
  upload works.
- Lit Review Run shares some infrastructure with Agent Review (LLM
  Provider, artifact builder) but lives in different tables and
  services. Future work that wants to express both as "LLM-driven
  advisory jobs" will need either an umbrella refactor or a
  carefully-named shared service module; the ADR explicitly defers
  that umbrella.
- v1 ships without quote-anchored highlights, which is a visible
  feature gap relative to academic literature tools. Users who expect
  Hypothesis-style annotation will find the v1 comment UI
  underpowered; v2 closes the gap.

**Open items resolved in this ADR:**

- Domain language for "a paper in the library": **Paper.**
- Naming and DDD relationship of the recommendation feature:
  **Lit Review Run**, sibling of Agent Review.
- PK type: **integer (`BIGSERIAL`)**, matching the codebase.
- Storage: **per-org bucket `bioaf-literature-{org}`**.
- Permission shape: **single `literature` resource with sub-actions**.
- Recommendation scoring: **continuous 0.0-1.0 plus derived bucket**.

**Open items deferred to other ADRs or to v2:**

- Quote-anchored highlights and the frontend PDF renderer (v2).
- Scheduling and the generic `ScheduledJob` primitive (separate v2 ADR
  yet to be drafted).
- Project-scoped Lit Review Run (v2).
- Saved searches with alerts (v2).
- Literature as input to Agent Review payload (ADR-057, this same
  pass).
