# ADR-057: Literature as Input to Agent Review

**Status:** Accepted
**Date:** 2026-05-18
**Deciders:** Brent (repository owner)

---

## Context

[ADR-055](../decisions/ADR-055-agent-review-advisory-entity.md)
established Agent Review as an advisory entity that hangs off a
Pipeline Run or Experiment and produces a severity-coded review note.
The Agent Review prompt is assembled by
`backend/app/services/agent_review_artifact_builder.py:build_for_run()`
from a section catalog
(`backend/app/services/agent_review_section_catalog.py`) that pulls in
experiment metadata, run parameters, output JSON, QC report text,
sample metadata, and errors. That prompt artifact is the only user
data that ever leaves the org under any LLM code path (per
[ADR-052](../decisions/ADR-052-llm-integration-trust-boundary.md)).

[ADR-056](ADR-056-literature-library-domain-model.md) introduces the
Literature Library as a new first-class data type. The original
Literature planning identified "Literature as input to Agent Review" as
the headline differentiator vs commodity literature managers: the lab's
own annotated and curated papers should be available as context to the
LLM reviewing an experiment or pipeline run.

This ADR specifies how that integration works:

- Which Literature inputs are bundled.
- How the user controls inclusion (toggles, defaults, overrides).
- How the new section is added to the artifact builder without
  modifying the existing Agent Review flow.
- How citations in the LLM output are post-processed back into
  clickable links to the Literature Library.
- How the token budget is bounded and surfaced.

The decision is additive to ADR-055. No existing Agent Review behavior
changes when the new toggles are off.

---

## Decision

### A new optional artifact section: "Associated Literature"

`agent_review_artifact_builder.build_for_run()` gains an additional
section, registered in `agent_review_section_catalog`, named
**`associated_literature`**. The section is skipped entirely when all
three Literature input toggles are off for the scope being reviewed.

The section is appended to the prompt after the existing experiment-
and run-context sections. Its template (Markdown):

```
## Associated Literature

The following papers are associated with this experiment.
When you note consistency or contradiction with prior work,
cite the specific paper and quote.

### Paper 1
Title: [title]
Authors: [authors]
Year: [year]
Journal: [journal]
DOI: [doi]

Abstract:
[abstract]

Team comments:
- Comment by [user] on [date]: [body]
- Comment by [user] on [date]: [body]

Full text:
[extracted text, if full text toggle is on]

### Paper 2
...
```

The "cite the specific paper and quote" instruction is part of the
template and is what enables the citation post-process step (below).

### Three toggles with defaults

The org admin configures three toggles at Settings > Agent Review >
Literature Inputs:

| Toggle | Default | Includes |
|---|---|---|
| `abstracts` | On | Title, authors, year, journal, DOI, abstract per Paper |
| `comments` | On | All non-deleted threaded comments on the Paper, flattened to a single list under the Paper |
| `full_text` | Off | Extracted text from `papers/{paper_id}/extracted.txt`, only for Papers with `has_full_text = true` |

Each toggle is a boolean stored in a new
`agent_review_literature_config` table:

```sql
CREATE TABLE agent_review_literature_config (
    id                  BIGSERIAL PRIMARY KEY,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    scope_type          TEXT NOT NULL,   -- 'org' | 'experiment' | 'project'
    scope_id            INTEGER,         -- null when scope_type = 'org'
    abstracts_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    comments_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    full_text_enabled   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by_user_id  INTEGER NOT NULL REFERENCES users(id),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX agent_review_lit_cfg_uniq
    ON agent_review_literature_config (organization_id, scope_type, COALESCE(scope_id, 0));
```

Resolution order at run time: scope-specific row -> parent-project row
(when reviewing an experiment) -> org-level row -> built-in defaults.

Only `admin` and `comp_bio` can mutate the config rows.

### Paper selection

The set of Papers considered for inclusion:

- **Default** when Agent Review runs on a Pipeline Run or Experiment:
  Papers with an active `literature_associations` row where
  `scope_type = 'experiment' AND scope_id = <the experiment id>`. For a
  Pipeline Run, the parent Experiment's papers.
- **User option** on the Agent Review launch form:
  `expand_to_project = true` adds Papers associated with
  `scope_type = 'project' AND scope_id = <the experiment's parent project>`.

Dismissed Papers (active row in `literature_paper_dismissals`) are
filtered out unconditionally.

### Ordering within the section

Papers are ordered so the most-curated content is at the top:

1. Papers uploaded by humans (`provenance = 'user_upload'`) with at
   least one non-deleted comment.
2. Papers uploaded by humans without comments.
3. Papers added by user-run ad-hoc search
   (`provenance = 'source_search'`).
4. Papers added by Lit Review Run
   (`provenance = 'lit_review_run'`, status `accepted`).

Within each tier, ordered by `publication_date DESC, created_at DESC`.

### Token budget

A hard cap defaults to **100,000 tokens** for the Literature section
total. The cap is a per-org setting at Settings > Agent Review >
Literature Inputs > "Maximum tokens for Literature section".

When the assembled Literature section exceeds the cap, truncation
happens from the bottom of the ordered list: drop whole Papers (do not
mid-truncate a Paper's body). The resulting Agent Review records a
warning in the run output: `"N papers truncated from Literature
payload due to token budget."`

Projected token count is surfaced in two places:

- At toggle time (Settings > Agent Review > Literature Inputs): a
  rough estimate based on the average Paper size and the org's typical
  experiment association count.
- At Agent Review launch time (the run launch form): the actual token
  count for the specific scope being reviewed, alongside the projected
  total cost via the active LLM Provider's rate.

A small `literature_token_budget` helper module
(`backend/app/services/literature/token_budget.py`) implements the
counter. It uses the existing per-provider tokenizer where available
(per [ADR-053](../decisions/ADR-053-llm-provider-abstraction.md));
otherwise it falls back to a 4-char-per-token heuristic.

### Citation post-process

After the LLM returns its Agent Review response, the existing
`backend/app/services/agent_review_response_parser.py` runs. This ADR
adds a new post-parse step that scans the response body for DOIs (and
optionally PMIDs) and, for each that matches an active
`literature_papers.doi` row in the org, rewrites the reference into a
Markdown link to the paper detail view:

```
[10.1038/s41592-024-00000-0](/data/literature/papers/12345)
```

DOIs that do not resolve to a local Paper render as plain links to
`https://doi.org/{doi}` (or are left untouched, depending on the link-
rewriting setting).

The post-process step is idempotent and never modifies the LLM's
analytical content; it only rewrites reference strings.

### Audit log

The Agent Review job row records, in its existing details JSONB, which
Papers were included in the prompt payload (paper ids and the toggles
state at run time). This sits alongside the existing snapshots of
provider, model, and prompt template version
(per ADR-055), so a later re-read of the audit log can reconstruct
the exact Literature payload context for any Agent Review run.

---

## Rationale

**Why an additive section rather than a separate prompt?** The
existing Agent Review prompt is the trust-boundary unit (per ADR-052):
exactly one outbound LLM call per Agent Review, with the prompt
artifact persisted in GCS. Splitting Literature into a second LLM call
would double the call count, complicate the trust audit, and force
us to design how the two responses combine. Appending a section is
the minimal change.

**Why three toggles instead of one master switch?** Different orgs
will have different appetites for cost and noise:

- A small lab may want abstracts but never full text (cost-sensitive).
- A team with high-confidence curated annotations may want comments
  but not abstracts (annotations carry the signal).
- A regulated environment may want neither and treat Literature as
  display-only.

Three toggles cover these without needing a free-form mask language.

**Why default `full_text = off`?** Full text inflates token cost by
roughly 10x-50x relative to abstracts. Even small libraries can exceed
the budget. Opt-in is the safe default; orgs that want it can flip it.

**Why the ordering rule?** Token budgets bite. Tail truncation should
discard the least-curated content (LLM-recommended-but-unread
papers), not the team's hand-annotated favorites. The ordering rule
encodes that priority.

**Why DOI -> link post-process instead of asking the LLM to format
links?** Asking the LLM to produce links pollutes its analytical task
and invites hallucinated URLs. Post-processing the response is
deterministic, auditable, and idempotent.

**Why a separate `agent_review_literature_config` table instead of
columns on `organizations` or `experiments`?** A per-scope config is
inherently a small lookup. Adding columns to the existing tables
couples them to a feature that some orgs may not use. A dedicated
table also gives us audit-log granularity at the right level
(`entity_type = 'agent_review_literature_config'`).

**Why is this not just part of ADR-056?** ADR-056 introduces the
Literature Library as its own thing: it stands alone even if the Agent
Review integration is never built. ADR-057 is the explicit decision to
extend Agent Review with a Literature-aware section. Keeping them
separate keeps each ADR small, focused, and independently rejectable.

---

## Consequences

**Easier:**

- Implementation slots into the existing Agent Review pipeline without
  disturbing any of the structures from ADR-055.
- Future work that wants to extend Agent Review with another input
  type (e.g., Reference Dataset notes, prior Agent Review summaries)
  has a clear precedent: add a section, add a toggle, add a config row
  type.
- The token budget infrastructure is reusable for Lit Review Run cost
  estimation.

**Harder:**

- Every Agent Review prompt with Literature on is larger and slower
  than before. Orgs that turn full text on for projects with many
  papers will see token bills grow.
- The DOI link rewrite step is one more place where the LLM output
  can be modified before display. The change is constrained
  (rewriting reference strings only) and auditable in the raw response
  preserved in GCS, but it does mean the UI display does not equal
  the LLM's literal output. The Agent Review tab should be able to
  toggle "show raw response" to surface the original.
- A new config table is introduced
  (`agent_review_literature_config`), with its own seed, audit, and
  permission story. Permission to mutate the config is `admin` and
  `comp_bio` (matching `literature.configure_sources`); permission to
  read is `view`.
- The relationship between ADR-055 and ADR-057 means a future change
  to the Agent Review prompt template must consider both the
  base sections and the Literature section ordering rules.

**Open items deferred from this ADR:**

- Whether the citation post-process should be opt-in per org (in case
  a regulated environment requires exact LLM output preservation in
  the displayed response). v1 ships it on by default with a
  "show raw response" affordance on the Agent Review tab; v2 may
  introduce an org-level toggle if demand exists.
- Whether comments at the LLM should be name-attributed or
  anonymized. v1 keeps the user name; an "anonymize comments in
  outbound prompts" org toggle is a candidate for a later ADR.
- Whether Lit Review Run results should themselves be included as a
  separate section (rather than the accepted recommendations folding
  into the regular Papers list). v1 treats accepted recommendations
  as ordinary Papers in tier 4 of the ordering rule.
