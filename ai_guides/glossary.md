# Glossary

The canonical, repository-wide domain language. One global glossary for the whole
repository.

## Authority

This glossary supersedes all other documents and all code, save for reality itself.
If code, an ADR, or a comment uses a term differently, the code is wrong, not the
glossary. If reality proves a term wrong, the glossary is corrected and everything
else follows.

Changes to this file require approval from the `ai_guides/` code owner.

## How to use it

- Before naming a concept in code, tests, ADRs, specs, commits, or discussion, check
  here for the canonical term.
- If the concept is not here and it is a real domain concept, it belongs here. Add it
  (subject to owner approval).
- If you find a term used inconsistently in code you are already modifying, conform it
  (see the boy-scout rule in [domain-language.md](domain-language.md)).

## Term entry format

Each term is a level-3 heading followed by a definition. Keep definitions to behavior
and meaning, not implementation.

```markdown
### Term Name

One or two sentences. What it is, what it is not. Reference related terms by name.
```

## Terms

### Access Log

A record of resource access events (reads, writes, deletes) with request metadata, for
security monitoring and access pattern analysis. Distinct from [Audit Log](#audit-log)
(mutations for compliance) and [Activity Feed](#activity-feed) (UI-facing timeline).

### Activity Feed

A human-readable event stream for the UI timeline, summarizing user-visible actions
such as pipeline completions and snapshot creations. Distinct from [Audit Log](#audit-log)
and [Access Log](#access-log); not a compliance or security record.

### Agent Review

An advisory, severity-coded note produced by an LLM in response to one of the
standardized review buttons on a [Pipeline Run](#pipeline-run) or
[Experiment](#experiment). Not provenance and not part of the scientific record:
a scientist may act on a flag, but the action they take (a rerun, a sample
reclassification) is what enters provenance. Lives in the Agent Review tab on
the entity it pertains to. Abstracts and comments from associated [Papers](#paper)
may be bundled into the Agent Review's prompt artifact when the org's
Literature inputs are enabled (see [ADR-057](../decisions/ADR-057-literature-as-input-to-agent-review.md)).

### Analysis Snapshot

_Pending definition._ A model named `AnalysisSnapshot` exists in code; the term has not
been confirmed by the domain owner. Do not use this term in new work until it is defined
here.

### API Key

A credential for programmatic access to bioAF, attached to a service-account [User](#user)
within an [Organization](#organization). Stores hashed key material, scopes, a
revocation timestamp, and a last-used timestamp. Authenticates calls to the public
Integration API.

### Audit Log

An immutable record of data mutations: who changed what, with before/after state, for
compliance and forensics. Distinct from [Access Log](#access-log) (resource access) and
[Activity Feed](#activity-feed) (UI timeline).

### Auto-ingest

A GCS event-driven process that watches a dedicated ingest bucket. On file arrival it
parses the filename via a [Naming Profile](#naming-profile), resolves or auto-creates
entities (in [Unclaimed](#unclaimed) status), checksums, links the file, copies it to
permanent storage, and emits an ingest event.

### BioAF Adapter Layer (BAL)

A set of interface contracts that decouple application logic from infrastructure
providers. Three provider categories: Compute Provider, Storage Provider, Notebook
Provider. The deployed stack today is Kubernetes (GKE) plus GCS; SLURM plus NFS is
stubbed.

### Compute Session

The canonical parent term for any on-demand interactive compute environment. Stored
in one table, distinguished by type. Two canonical subtypes:
[Notebook Session](#notebook-session) (Jupyter or RStudio, browser-based) and
[Work Node](#work-node) (SSH-accessed Linux pod). `NotebookSession` as a parent alias is
deprecated.

### CRO (Contract Research Organization)

An external lab that performs library prep and/or sequencing and delivers processed
data files to the customer, typically with structured filenames encoding
project/experiment/sample/date/type. The party whose deliveries [Naming Profiles](#naming-profile)
and [Auto-ingest](#auto-ingest) are built to handle.

### Custom Pipeline

A user-authored pipeline that is executed in a Linux node configured using Conda, with
image configurations stored in [Environments](#environment). The user may store
pipeline code in GitHub in a repo that is passed to their image, or they may pass in a
code blob directly. Each custom pipeline is owned by an [Organization](#organization)
with its own version history (see [Custom Pipeline Version](#custom-pipeline-version)).

### Custom Pipeline Version

A specific release of a [Custom Pipeline](#custom-pipeline) that locks code,
[Environment Version](#environment-version), resource requests, and variable schema.

### Environment

A named, versioned compute environment template (OS, tools, libraries) owned by an
[Organization](#organization), with one of three types: notebook, work_node, or
pipeline. Referenced by [Compute Sessions](#compute-session) and
[Custom Pipeline Versions](#custom-pipeline-version) via
[Environment Versions](#environment-version).

### Environment Version

An immutable build of an [Environment](#environment): the Dockerfile or conda
definition, the built image URI, and the build status. Each
[Compute Session](#compute-session) and
[Custom Pipeline Version](#custom-pipeline-version) pins to a specific Environment
Version.

### Experiment

A research study that owns [Samples](#sample) and progresses through a fixed status
lifecycle: `registered` -> `library_prep` -> `sequencing` -> `fastq_uploaded` ->
`processing` -> `pipeline_complete` -> `reviewed` -> `analysis` -> `complete`. The
primary entity all other layers reference; mandatory (unlike [Project](#project)). Has
a code, belongs to an [Organization](#organization), optionally to a Project.

### LLM Provider

A configured back end for [Agent Review](#agent-review) inference. Four are
supported: OpenAI, Anthropic Claude, Google Gemini (hosted), and Gemma 4 (self-
hosted, runs inside the bioAF GCP project). Exactly one per [Organization](#organization)
is active at a time; switching is a settings toggle, keys for all four can
persist simultaneously.

### Lit Review Run

A single execution of an LLM-driven paper recommendation job scoped to an
[Experiment](#experiment). Uses the org's active [LLM Provider](#llm-provider) to
generate expansion queries and rank candidate papers, producing a queue of
[Literature Recommendations](#literature-recommendation). On demand in v1;
scheduled cadences are deferred to v2. Not an [Agent Review](#agent-review):
different output shape (queue of recommendations vs severity-coded advisory
note), different lifecycle, different table. See [ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### Literature Dismissal

An org-wide signal that a [Paper](#paper) should be excluded from default
[Literature Library](#literature-library) views, future [Lit Review Runs](#lit-review-run),
and any [Agent Review](#agent-review) Literature payload. Created by `admin` or
`comp_bio`. Reversible by `admin` only. See [ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### Literature Library

The org-scoped collection of [Papers](#paper) and their associations, comments,
reading status, and dismissals. Distinct from the [Reference Dataset](#reference-dataset)
registry; distinct from the file/document subsystem (papers live in a
dedicated per-org GCS bucket `bioaf-literature-{org}` and a dedicated set of
tables). See [ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### Literature Recommendation

A single LLM-scored paper produced by a [Lit Review Run](#lit-review-run),
with a continuous relevance score (0.0 to 1.0) and one-sentence reasoning.
Lifecycle: `pending -> accepted` (paper joins the [Literature Library](#literature-library)
associated with the scope) or `pending -> dismissed` (paper is dismissed
org-wide via [Literature Dismissal](#literature-dismissal)). See
[ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### Literature Source

An external bibliographic data source the [Literature Library](#literature-library)
can query. Four in v1: PubMed (NCBI), bioRxiv, Europe PMC, Semantic Scholar.
Per-org configuration of enablement and API key. API keys are stored
encrypted at rest via the `EncryptedString` pattern from
[ADR-047](../decisions/ADR-047-data-at-rest-encryption.md). See
[ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### `.md` Review Artifact

The standardized Markdown rollup of a single [Pipeline Run](#pipeline-run) (run
record, parameters, output JSON, QC report text, sample metadata, errors) built
on demand for [Agent Review](#agent-review) consumption and persisted in GCS
under that pipeline run's artifact directory. The only user data that ever
leaves the org under any LLM code path. Raw rows, FASTQ, file blobs, and
pipeline logs are never included.

### Naming Profile

A configurable template for parsing structured filenames, typically from a [CRO](#cro-contract-research-organization)
delivery, into bioAF entity codes. Defines a delimiter and ordered segments plus
mappings from parsed codes to [Project](#project) and [Experiment](#experiment) codes.
Multiple profiles can be active simultaneously; ambiguous matches flag for manual review.
_Volatile: the Naming Profile model is expected to change in the near future._

### Notebook Session

A [Compute Session](#compute-session) subtype: a browser-based Jupyter or RStudio
session.

### Organization

_Pending definition._ The top-level tenant entity owning all org-scoped records. Not
yet explicitly grilled with the domain owner. Use with care until confirmed here.

### Org Code Counter

A per-[Organization](#organization) auto-increment sequence generator. Maintains the
next value per code kind (e.g. project code, experiment code) so that human-readable
entity codes are unique and sequential within an Organization.

### Paper

A scholarly publication tracked in the [Literature Library](#literature-library),
identified by DOI (primary) or normalized title plus first-author and
last-author keys (fallback). Org-scoped, org-readable; immutable identifier,
mutable metadata. Not a [Reference Dataset](#reference-dataset) (those are
curated biological references such as genomes, annotations, indexes). May be
associated with one or more [Experiments](#experiment), [Projects](#project),
or globally to the [Organization](#organization). See [ADR-056](../decisions/ADR-056-literature-library-domain-model.md).

### Pipeline Catalog Entry

A pipeline registered in an [Organization](#organization)'s catalog and available to
launch. May be built-in (nf-core) or a wrapper around a [Custom Pipeline](#custom-pipeline).
Holds the parameter schema, default parameters, and QC config. The thing a user
"selects" to start a run.

### Pipeline Process

A single task or process within a [Pipeline Run](#pipeline-run) (one Nextflow process
execution). Records process name, task id, exit code, resource consumption, duration,
and stdout/stderr paths. Deleted with its parent run.

### Pipeline Run

A single execution instance of a pipeline (built-in or custom) against
[Experiment](#experiment) or [Project](#project) data. Tracks orchestration (the
Kubernetes job), parameters, cost estimate vs. actual cost, status, and retry/resume
lineage. Owns [Pipeline Process](#pipeline-process) records.

### Pipeline Trigger

A configured rule that launches a [Pipeline Run](#pipeline-run) in one of three modes:
manual (UI), event-driven (file ingest), or scheduled (cron). All modes pass through
the same budget-aware pre-flight check; if over budget, the run is queued as "pending
budget review."

### Project

A grouping of one or more [Experiments](#experiment) for cross-experiment analytical
work. [Samples](#sample) and Files may also be associated to a Project directly.
Optional, unlike Experiment.

### QC Dashboard

Generated quality-control metrics and visualizations for a single
[Pipeline Run](#pipeline-run). Aggregates per-sample and per-run metrics and plots,
built from the pipeline's QC config. Has its own generation status (generating /
complete / failed).

### Reference Dataset

A curated, versioned biological reference (genome, annotation, index, atlas, markers)
stored in GCS, scoped public or internal to an [Organization](#organization). Has
lifecycle status (active / deprecated / pending_approval / uploading / failed) and
supersession lineage. Associated to [Pipeline Runs](#pipeline-run). May also be
associated to [Notebook Sessions](#notebook-session) or [Work Nodes](#work-node) (the
relationship exists; not used today).

### Review Handoff

A lightweight, advisory review step where a bioinformatician verdicts a
[Pipeline Run](#pipeline-run)'s output quality: `approved`, `approved_with_caveats`, or
`needs_reprocessing`. Reviews are **not** gates: data remains accessible regardless.
Submitting a review is the formal handoff signal and notifies the experiment owner.

### Sample

An individual biological specimen belonging to one [Experiment](#experiment), carrying
phenotypic metadata (organism, tissue, treatment condition, chemistry version). Has its
own status lifecycle and an independent `qc_status` (`pass` / `warning` / `fail`). Can
be assigned to a [Sample Batch](#sample-batch) and a [Sequencing Batch](#sequencing-batch).
Samples do not have a parent sample.

### Sample Batch

A library-prep cohort, scoped to one [Experiment](#experiment), recording prep date,
operator, and instrument. Distinct from [Sequencing Batch](#sequencing-batch). Plain
"Batch" is disallowed; always use one of the two specific terms.

### Sequencing Batch

A sequencer-output cohort, scoped to the [Organization](#organization) (cross-experiment),
tracking file-ingestion progress with its own status. Distinct from
[Sample Batch](#sample-batch). Plain "Batch" is disallowed; always use one of the two
specific terms.

### Severity (Agent Review)

The red/orange/green grading returned in the [Agent Review](#agent-review)
response's JSON header and used to color-code the card. Red is "I found a major
concern," orange is "something is strange here," green is "no concerns flagged."
A fourth value, `unknown`, marks a parse failure: the response body is preserved
but the structured header could not be read.

### Stale (Agent Review)

A flag on an experiment-level [Agent Review](#agent-review) indicating that the
[Experiment](#experiment) now contains a [Pipeline Run](#pipeline-run) that was
not in the review's included-runs list. Computed at query time, not persisted.
Adding a [Sample](#sample) alone does not trigger stale; only adding a pipeline
run does. Single-run reviews are never stale.

### Unclaimed

A status applied to auto-created entities ([Project](#project), [Experiment](#experiment),
[Sample](#sample)) produced by [Auto-ingest](#auto-ingest) when a
[Naming Profile](#naming-profile) parses a code that does not map to an existing entity.
The entity is created with a visual badge and minimum metadata, and is later "claimed"
(metadata completed, ownership assigned) by an authorized user.

### User

_Pending definition. To be grilled._

### Webhook Delivery

A single dispatch attempt belonging to a [Webhook Subscription](#webhook-subscription),
with retry state and HTTP response capture. Distinct from the Subscription itself.

### Webhook Subscription

A per-[Organization](#organization) registration of an external URL, secret, and
subscribed event types. Owns [Webhook Delivery](#webhook-delivery) records. Distinct
from a Delivery.

### Work Node

A [Compute Session](#compute-session) subtype: an SSH-accessed Linux pod for
interactive work. Backed by an ephemeral Kubernetes Pod with GCS FUSE data mounts.

## Deprecated term map

When a term is renamed, the old term is recorded here so that agents reading older
code, ADRs, or git history can translate. Historical artifacts are not rewritten; they
are read through this map. New work uses the current term only.

| Deprecated term | Current term | Reason | Date |
|---|---|---|---|
| NotebookSession (as parent of all session types) | [Compute Session](#compute-session) | Compute Session is now the canonical parent term; Notebook Session is one of its two subtypes. | 2026-05-14 |
