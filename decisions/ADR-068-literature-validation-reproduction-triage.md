# ADR-068: Literature Validation as a Reproduction-Triage Screen

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** Brent (repository owner)

---

## Context

Teams build on published papers. Some of those papers do not hold up: the deposited
data is missing or unusable, the methods are too thin to reproduce, or the reported
numbers do not reproduce from the deposited data. Deciding what is worth building on is
today a manual, expensive, and inconsistent judgment.

bioAF already has the pieces needed to attempt a reproduction: a Literature Library
([ADR-056](ADR-056-literature-library-domain-model.md)), `nf-core/fetchngs` in the
pipeline catalog for pulling public accessions, the pipeline-run + adapter machinery for
executing nf-core workflows, QC templates + the QC dashboard service for extracting
computed metrics, and the agent-review LLM job/parse patterns
([ADR-055](ADR-055-agent-review-advisory-entity.md)). What was missing is the orchestration
that reads a paper, reproduces its primary processing from the deposited data using an
equivalent nf-core pipeline, compares the computed QC numbers against the paper's claims,
and classifies how well the paper holds up.

This is a significant, multi-part feature (paper comprehension, a human approval gate,
data fetch + execution + metric extraction, comparison + attribution + classification,
surfacing + reporting). It was de-risked with throwaway spikes (full-text extraction
reliability; `nf-core/fetchngs` end to end) and built in phases. The working design,
specs, and phase history live in the gitignored `local/lit_validation/` working space.
This ADR promotes the load-bearing architectural decisions to the committed record.

The decision is additive: no existing behavior changes. It reuses the provenance report
system ([ADR-037](ADR-037-provenance-reporting.md)), the custom RBAC
([ADR-032](ADR-032-custom-rbac.md)), the immutable audit log
([ADR-009](ADR-009-immutable-audit-log.md)), and the LLM trust boundary + provider
abstraction ([ADR-052](ADR-052-llm-integration-trust-boundary.md),
[ADR-053](ADR-053-llm-provider-abstraction.md)).

---

## Decision

### The feature is a data-and-processing integrity screen at Level 2, not a conclusion oracle

Reproduction has three altitudes: (1) the data is present and usable, (2) the QC-level
numbers reproduce within tolerance, (3) the biological findings reproduce. Level 3 is
mostly unreachable with nf-core (it lives in bespoke downstream analysis the pipelines do
not cover), so **the target altitude is Level 2: do the computed QC metrics agree with
the paper's reported QC metrics.** The system tells you whether a paper's data is present,
usable, and reproduces to the QC numbers the paper reports. It does not adjudicate the
paper's downstream scientific conclusions. This scope is deliberate and is what makes the
verdict defensible.

### A six-bucket factual taxonomy, with no "bad" label

A `ValidationStudy` classifies into exactly one of six buckets:

| Bucket | Meaning |
|---|---|
| `validated` | Re-ran from deposited data; QC metrics agree within tolerance. |
| `not_validated` | Re-ran cleanly, but QC contradicts the paper, and our side was ruled out. |
| `missing_data` | No usable raw data deposited (none, dead links, or processed-only). |
| `missing_methods` | Methods too thin to construct a reproduction plan. |
| `not_reproducible` | Method has no installable nf-core equivalent (out of our scope, not a judgment on the paper). |
| `inconclusive` | Divergence we could not attribute to either side. Needs a human. |

The taxonomy is two-axis on purpose: "no data deposited" (a transparency gap) and
"results contradict the claim" (evidence of a problem) are different things, and lumping
them into one "bad" label would mislead and could defame correct work. **The classifier
states facts and evidence; a user-set policy or a human decides what to do with each
bucket.** There is no "bad" label.

### A `ValidationStudy` aggregate with a forward-only state machine

One `ValidationStudy` row per reproduction attempt (org-scoped, audited), modeled on the
existing `*_STATUS_TRANSITIONS` convention used by Experiment and Sample. States run
`requested -> acquiring_text -> reading -> plan_ready -> acquiring_data -> setup ->
running -> extracting -> comparing`, terminating in `classified` (carries a bucket),
`plan_declined`, or `error`. Transitions are forward-only; `error` is reachable from every
active state (any step can hit infra failure and is retryable); and `reading`/
`acquiring_data` have early exits straight to `classified` so a study can reach a verdict
without ever running compute (no accession -> `missing_data`; thin methods ->
`missing_methods`; no nf-core equivalent -> `not_reproducible`; fetched-but-unusable data
-> `missing_data`).

### Orchestration is a periodic polling driver, not an event subscriber

A background tick (`ValidationDriverService.advance_active_studies`, ~30s, registered in
the app lifespan like the pipeline monitor and auto-run) advances active studies by
reading committed pipeline-run state. It is deliberately **polling, not an event
subscriber**: `pipeline_run`/monitor emit completion via `asyncio.create_task(...)`, which
can run before the monitor's own commit, so a subscriber opening a fresh session may not
see the committed samples/QC. Polling committed state removes the race, and the driver
re-runs the (idempotent) ingest + FASTQ-attach itself so it does not depend on the
monitor's timing.

### Reproduction is one deterministic recipe over shared primitives

Data fetch (`nf-core/fetchngs`, D1), experiment/sample setup (D2), pipeline execution
(D3), and QC metric extraction (E1) are **existing machinery, reused, not forked.** The
driver is a fixed preset recipe over those primitives. The net-new intellectual work
concentrates in three places only: paper comprehension (B2 extractor + B3 pipeline
mapper), the comparison/attribution/classifier core (E2/E3/E4), and the human approval
gate (C1). The same action primitives are intended to be shared with a future
agent-driven pipeline-run feature rather than reimplemented.

### A human ratifies the plan before any compute (C1)

Before any spend, a scientist reviews and ratifies a `ReproductionPlan`: the deposited
accession(s), the chosen nf-core pipeline + version + params + reference build (with the
mapper's confidence and rationale), and the comparison targets. They can edit or decline.
This gate respects existing budget controls, turns the riskiest AI step (method ->
pipeline mapping) into a human-ratified recommendation, and gives the eventual verdict a
defensible provenance. It is gated by a new `lit_validation` RBAC resource
(request / approve / view actions) via the existing `require_permission` dependency.

### The LLM proposes; deterministic rules decide the verdict

The classifier (E4) is rule-based and auditable; **the LLM never picks the bucket.** The
comparison engine (E2) is tolerance-based, never exact-match, and owns a deterministic
alias/normalization + unit-reconciliation layer that bridges the paper-claim key
vocabulary to the controlled QC-metric vocabulary (the two do not naturally join). The
attribution layer (E3) must clear our side (a confident nf-core equivalent AND a
recognized reference build) before any divergence is allowed to become `not_validated`;
if it cannot, the honest verdict is `inconclusive`, not `not_validated`. Because the
mapper defaults to `partial` equivalence, `not_validated` is rare by design, which is the
high bar the strongest negative claim should meet.

### Hybrid verdict policy: auto-finalize a clean pass, hold everything else for a human

At `comparing` the classifier runs once. A clean, solid `validated` (enough comparable
metrics agreeing, no coverage gap) auto-finalizes to `classified`. Everything else
(divergence, `inconclusive`, `not_validated`, or a validated-but-thin result where a lone
metric agrees amid uncomparable claims) **holds at `comparing` with the suggested verdict
and its full evidence recorded, for a human to ratify or override** via the classify
control. This keeps the machine confident where it is confident and defers to a human
everywhere else.

### Paper-to-run provenance via a `validation_study` provenance entity type (A3 + F3)

Provenance and export reuse the existing provenance report service
([ADR-037](ADR-037-provenance-reporting.md)) through a new `validation_study` entity type
rather than a bespoke exporter. `GET /api/validation-studies/{id}/provenance/report`
(gated `lit_validation:view`, audited, all existing formats) exports the study and its
evidence bundle, rendering the full reproduction chain **source paper -> reproduction plan
-> experiment -> data run (fetchngs) -> analysis run (rnaseq/scrnaseq)** alongside the
computed-vs-claimed comparison and the classifier verdict. The reproduction experiment's
own provenance report carries a reverse "Reproduces Paper" link. All linkage data already
lives on the `ValidationStudy` (paper id, source DOI/accession, experiment id, the two run
ids, evidence bundle); the provenance addition is a rendering layer, not new data.

### QC extraction dispatches per pipeline type, and an unmapped type is honest

Computed metrics come from the analysis run's QC dashboard. Metric extraction dispatches
to the resolved pipeline template's own `extract()` contract by exact template name. A
pipeline type with no registered extractor returns **honest empty metrics**, not another
type's parser misapplied, so `lit_validation` returns `inconclusive` for that type rather
than a fabricated verdict. Adding a new pipeline type's extractor is a drop-in module.
Bulk and single-cell RNA-seq (the common cases) have real extractors; the other catalog
types are drop-in follow-ups.

---

## Rationale

**Why an integrity screen, not a conclusion oracle?** Level 3 (biological findings) is
mostly unreachable with nf-core, and overclaiming there would produce confident-but-wrong
judgments about papers. Level 2 (QC agreement) is exactly the intersection of what nf-core
produces and what papers report, and it is a defensible, honest claim. Scoping down is
what makes the verdict trustworthy.

**Why no "bad" label?** Conflating "untestable" (a transparency gap) with "false"
(evidence of a problem) is the core design risk: it misleads users and could defame
correct work. Two axes and six factual buckets keep the system stating evidence, with
judgment pushed out to a policy or a human. This is the single most important design
constraint of the feature.

**Why deterministic rules for the verdict instead of asking the LLM?** The verdict is the
load-bearing output; it must be auditable and reproducible. Letting the LLM pick the
bucket would make the verdict non-deterministic and unexplainable. The LLM is excellent at
the comprehension task (reading prose into a structured plan and claims) and is confined
to it; the classification is pure rules over the comparison and attribution.

**Why polling, not events, for the driver?** The completion emit races the monitor's
commit, so an event subscriber can wake up before the data it needs is visible. Polling
committed state is race-free and matches the two existing background loops. The small
latency cost (a tick interval) is irrelevant for a minutes-to-hours reproduction.

**Why reuse the primitives instead of a bespoke pipeline?** Data fetch, setup, execution,
and QC extraction are already correct, tested, and permission/audit-wrapped. Forking them
would duplicate the hardest-to-get-right machinery. Framing reproduction as one
deterministic recipe over shared primitives keeps the net-new surface small and lets a
future agent-driven feature drive the same primitives.

**Why the human gate before compute?** The method-to-pipeline mapping is the riskiest AI
step and compute is not free. A ratification gate turns the AI's mapping into a
human-approved recommendation, respects budget controls, and gives the verdict a
defensible chain of custody.

**Why the hybrid auto-finalize policy?** A clean pass with good coverage is
unambiguous and not worth a human's time; everything else (divergence, thin coverage,
unattributable difference) genuinely needs judgment. Auto-finalizing only the clean pass
maximizes automation without ever hiding a debatable call from a human.

**Why a `validation_study` provenance entity type instead of a bespoke exporter?** The
provenance report system already renders per-entity reports in every format (JSON /
Markdown / PDF / CSV). Adding an entity type gives the study report, the paper-to-run
chain (A3), and the multi-format export (F3) in one addition, consistent with every other
entity's report, rather than a one-off export path.

**Why honest empty metrics for an unmapped pipeline type?** The earlier failure mode was
silent: a bulk run was run through the single-cell extractor and produced all-null
metrics, which starved the classifier into a false `inconclusive` for a subtle reason.
Returning honest empty metrics for an unmapped type makes the gap explicit and correct,
and makes each new type a clean drop-in.

---

## Consequences

**Easier:**

- Reproduction triage becomes a first-class, auditable workflow: a researcher can trigger
  validation from a paper, watch it run, read a defensible bucket with computed-vs-claimed
  evidence, and export a provenance report, entirely in the UI.
- The verdict is defensible end to end: a human-ratified plan, deterministic
  classification, attribution that guards the strongest negative claim, and a provenance
  report linking paper to runs.
- Adding breadth is incremental: a new pipeline type needs a drop-in QC extractor, its
  controlled metric keys in the classifier specs, and mapper coverage. Nothing else moves.
- The shared action primitives (recommend-pipeline, import-by-accession, setup, launch,
  metrics, explain) are available to a future agent-driven pipeline-run feature.

**Harder:**

- Coverage of assays beyond RNA-seq is gated on per-pipeline-type QC extractors. Until a
  type has one, `lit_validation` honestly returns `inconclusive` for it.
- nf-core equivalence is usually partial, not 1:1 (custom references, legacy toolchains,
  probe chemistries). Partial equivalence confounds exact metric reproduction and is why
  `not_validated` sits behind the attribution guard; it also means many real papers land
  in `inconclusive` rather than a strong verdict.
- Papers report QC metrics inconsistently, so the comparison engine frequently has sparse
  or zero comparable targets. The "reproduced but few comparable metrics" (thin-coverage)
  outcome is a real, common state that the hybrid policy holds for a human.
- Per-metric tolerances are first-pass defaults calibrated against a narrow set of real
  papers; widening the calibration set is ongoing hardening, not a one-time task.
- Compute cost and demo-cluster capacity are real: a real reproduction pulls the whole
  study's FASTQ and runs a full nf-core alignment, which the C1 gate and budget controls
  bound but do not eliminate.

**Open items deferred from this ADR:**

- Full-text acquisition currently relies on Europe PMC (open access only); non-OA papers
  need a pasted body until published-mirror / PDF fallback routes are added.
- The paper-read (LLM extraction) step runs synchronously in-request; moving it to the
  agent-review async-job pattern is deferred polish.
- An optional LLM "evidence narrative" (advisory prose behind the deterministic verdict)
  is specified but not built; the deterministic reasoning string already renders in the
  report.
- A filter policy that routes buckets in the literature workflow (e.g. surface only
  `validated`) is deferred; it deliberately keeps judgment in policy, out of the classifier.
