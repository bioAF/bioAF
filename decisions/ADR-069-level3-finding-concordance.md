# ADR-069: Level-3 Finding Concordance for Literature Validation

**Status:** Proposed
**Date:** 2026-07-22
**Deciders:** Brent (repository owner)

---

## Context

[ADR-068](ADR-068-literature-validation-reproduction-triage.md) scoped literature validation
deliberately at **Level 2**: re-run a paper's deposited data through one nf-core pipeline,
extract scalar QC metrics, and compare them to the paper's reported QC numbers within
tolerance. Level 3 (the paper's biological finding reproduces) was declared "mostly unreachable
with nf-core," because nf-core stops at a counts / consensus-peak matrix plus QC, not at the
paper's downstream conclusion. As a result, every differential claim a paper actually makes
(differential expression, differential accessibility, gained/lost peaks) is surfaced honestly as
`not_computed` and forces `inconclusive`.

The repository owner has decided to extend the feature to Level 3: validate a paper's actual
reported finding, not only that its processing reproduces to the QC numbers. This ADR records the
load-bearing decisions for that extension. It is additive: the Level-2 path (ADR-068) is
unchanged and still runs; Level 3 is a higher-altitude finding layered on top.

The substrate already exists. nf-core/rnaseq emits a gene-level counts matrix and nf-core/chipseq
and nf-core/atacseq emit a consensus-peak set plus a peak-by-sample count matrix; all are already
registered as `count_matrix` File rows by the output collector. bioAF's notebook environment
already ships the differential toolchain (DESeq2, edgeR, limma, DiffBind, Seurat). What is missing
is the orchestration that runs the differential analysis, acquires the paper's own deposited result
set, and compares the two as sets.

## Decision

### The verdict altitude rises to a defensible slice of Level 3: differential-finding concordance

The system reproduces a paper's **primary differential finding** (differential expression for
RNA-seq; differential accessibility for ATAC/ChIP-seq) from the deposited raw data, and compares it
to the paper's **own deposited result set** by concordance. This is a bounded, honest slice of Level
3, not a universal conclusion oracle. Cluster identities, cell-type proportions, effect-size-model
replication, and pathway/GSEA narratives remain out of scope. Naming stays precise: "the paper's
reported differential finding reproduced (or did not), here is the evidence."

### The differential step runs in one general, reusable headless-notebook executor, not a per-assay pipeline

We explicitly reject building a bespoke Nextflow pipeline per assay for the differential step: it is
fragile and single-use, and the goal is to validate findings for **any** paper, not a fixed set of
assays. Instead we add a general platform primitive: **headless (parameterized) execution of a
template notebook, with its outputs captured as provenance-tracked files.** It reuses the existing
notebook image, notebook output provenance ([ADR-039](ADR-039-notebook-output-provenance.md)), and
file lifecycle ([ADR-040](ADR-040-notebook-file-lifecycle.md)); today notebooks are interactive-only,
so the net-new work is the headless execution trigger. A paper "type" reduces to two swappable pieces:
which analysis template runs, and which concordance comparator scores it. This primitive is scoped as
a shared capability (the future agent-driven pipeline-run feature and reproducible-analysis use cases
want the same thing), not lit_validation-internal.

### Ground truth is acquired hybrid: auto-fetch, human confirms at the C1 gate

The Level-3 comparison needs the paper's own result set (its deposited DEG table or differential-peak
list). We auto-fetch and parse it (GEO supplementary / journal supplementary) into a normalized set,
then present it to the scientist at the existing C1 approval gate to confirm or correct before any
compute. This keeps the riskiest, most heterogeneous input (like B1 full-text before it) under human
control while still automating the common case. A paper with no obtainable, human-confirmable result
set yields `not_computed`; the verdict caps at Level-2 behavior and never fabricates a set.

### Concordance is directional overlap plus an enrichment significance, both required

The comparator scores our reproduced set against the paper's set with two signals: the fraction of the
paper's significant hits we recover with **concordant direction** (up/down), and the **statistical
significance of the overlap** (hypergeometric / Fisher). Agreement requires both to clear a threshold.
Two comparator families cover the starting types: gene-set (RNA-seq) and genomic-interval-set
(ATAC/ChIP, reciprocal-overlap based). Thresholds are policy defaults, calibrated against real papers,
as the Level-2 tolerances are.

### The LLM proposes; deterministic rules still decide, and the strongest negative stays guarded

A concordance agreement is a first-class **finding-tier** result (spec-06 tier model), so it can earn a
Level-3 `validated`, which a scalar QC floor never could. The classifier remains rule-based; the LLM
does not pick the verdict. The attribution guard extends to the differential step: a low concordance
becomes `not_validated` only when our side is fully cleared (matched genome build/annotation, the
paper's stated thresholds applied, a comparable differential method); otherwise the honest verdict is
`inconclusive`. The hybrid auto-finalize policy is unchanged: a solid pass auto-finalizes, everything
else holds for a human.

### Starting types are RNA-seq DE and ATAC-seq DA

The two starting types are chosen to prove the framework generalizes across **both** comparator
families over the one execution engine: bulk RNA-seq differential expression (gene-set) and ATAC-seq
differential accessibility (genomic-interval-set). ChIP-seq differential binding follows nearly free
(same interval comparator, same consensus-peak substrate). scRNA-seq cluster-identity concordance is
deferred as the genuinely hard case; the framework must not preclude it.

## Rationale

**Why cross to Level 3 at all?** The feature's whole purpose is deciding what is worth building on. "The
data is the right organism and quality" (Level 2) is necessary but not sufficient; users ultimately want
to know whether the reported finding holds up. The substrate (matrices) and the compute (the notebook R
stack) already exist, which makes a bounded differential-concordance slice reachable now.

**Why a general notebook executor instead of per-assay pipelines?** A custom pipeline per assay is
fragile and does not generalize; the objective is any-paper coverage. A parameterized notebook executor
turns each new type into a drop-in (template + comparator), mirrors the Level-2 QC-extractor drop-in
model, and yields a primitive reusable outside lit_validation.

**Why hybrid ground-truth with a human confirm?** Deposited result tables are heterogeneous and the fetch
is fragile (the B1 full-text lesson). Auto-fetch handles the common case; the human confirm at the
already-existing gate keeps the ground truth trustworthy and the verdict defensible.

**Why overlap AND enrichment?** Overlap alone can be inflated by large sets or lenient thresholds;
enrichment significance calibrates for that. Requiring both, with concordant direction, is the defensible
definition of "the finding reproduced."

**Why keep the negative guarded?** Partial pipeline equivalence plus a different differential tool/version/
threshold can depress overlap for reasons that are our side, not the paper's. Guarding `not_validated`
behind full our-side clearance preserves the property that the strongest negative claim is one we can
stand behind.

## Consequences

**Easier:**

- The verdict can speak to the paper's actual reported finding, with the two result sets and their
  concordance as evidence, not only QC identity.
- A general headless-notebook execution primitive becomes available platform-wide (reproducible analyses,
  the future agent-driven pipeline-run feature), not just here.
- Adding a new type stays a drop-in: an analysis template plus a comparator choice, consistent with the
  Level-2 extractor model.

**Harder:**

- Ground-truth acquisition of deposited result tables is heterogeneous and partly manual; it is the
  primary reliability risk and needs a de-risking spike.
- Concordance thresholds and the peak interval-overlap definition are new policy surfaces to calibrate.
- The differential step adds compute (a headless analysis run per study) and new failure modes (notebook
  execution, id-namespace / genome-build normalization).
- scRNA-seq cluster-identity concordance remains unreached; the honest verdict there stays capped.

**Open items:**

- Concordance thresholds, the interval-overlap definition, and auto-fetch route coverage are calibration
  outputs, not fixed here.
- Whether effect-size correlation is added as a third concordance signal is deferred pending calibration.
- The working design, phasing, and test list live in the gitignored `local/lit_validation/`
  (spec-08); this ADR promotes only the load-bearing decisions.
