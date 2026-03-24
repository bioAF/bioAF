# ADR-006: Experiment Tracking as Architectural Foundation

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

Computational biology platforms typically treat infrastructure (compute, pipelines, notebooks) as the primary concern and experiment metadata as an afterthought — something users track in spreadsheets, ELNs, or not at all. This creates a disconnect between the bench scientists who generate samples and the computational biologists who analyze them, and makes it difficult to trace a result back to its source for publication or regulatory purposes.

bioAF's target users include bench scientists (who own sample metadata), bioinformaticians (who process data), computational biologists (who analyze data), and leadership (who need audit trails). A platform that only serves the computational team misses half the user base and the auditability requirement.

## Decision

Experiment tracking is a mandatory, first-class layer in the bioAF architecture — not an optional add-on. Experiments and samples are the primary entities that all other layers reference. Every FASTQ upload, pipeline run, notebook session, and visualization links back to an experiment and its samples.

The experiment tracker is part of the mandatory foundation (deployed with `bioaf deploy`) and is built in Phase 2 (weeks 4–6), immediately after the infrastructure skeleton, so that every subsequent layer can reference it from day one.

### Data Model Implications

- `experiments` and `samples` are core tables in the PostgreSQL database
- `pipeline_runs`, `compute_sessions`, and `published_visualizations` all have foreign keys to `experiments`
- `sample_files` links samples to their data files (FASTQs, BAMs, h5ad)
- `audit_log` records every action on every entity with immutable append-only semantics

### Status Machine

Experiment status transitions automatically as data flows through the platform:

- FASTQ upload → status moves to "fastq_uploaded"
- Pipeline launch → "processing"
- Pipeline complete → "analysis"
- (Status can also be set manually by users)

### Bench Scientist Interface

The experiment registration UI is designed for bench scientists: form-based, no technical knowledge required, with configurable required fields and templates for recurring experiment types. Batch CSV upload is supported for experiments with many samples.

## Rationale

- **Bench scientists are the source of truth for sample metadata.** If the platform doesn't capture this at the point of origin, it's lost by the time it matters. The most common complaint from computational biologists is chasing down sample metadata after the fact.
- **Auditability is a product requirement, not a nice-to-have.** Small biotechs publishing papers need provenance from sample to figure. Biotechs pursuing regulatory submissions need complete audit trails. Building this into the architecture from day one is dramatically cheaper than retrofitting it later.
- **Experiment tracking is the connective tissue.** Without it, pipeline runs, notebook sessions, and visualizations are disconnected artifacts. With it, the platform tells a coherent story: "This cell atlas was generated from these 12 samples in experiment X, processed through pipeline version Y with parameters Z, and analyzed in notebook session W."
- **Building it in Phase 2 prevents the "tack-on" problem.** If experiment tracking is added after compute and pipelines are already built, it becomes a second-class citizen with bolted-on linkages instead of native references.

## Consequences

- The PostgreSQL schema must be designed experiment-first. All other entities reference experiments and samples.
- The bioAF API must enforce referential integrity: you can't upload a FASTQ without linking it to an experiment, and you can't launch a pipeline without specifying which experiment's data to process.
- The audit log table has INSERT-only permissions at the database role level (not just application level). No UPDATE or DELETE grants exist for any role.
- The experiment dashboard is the home page for bench scientists and leadership. Compute and pipeline views are secondary navigation for comp bio users.
- Custom metadata fields must be supported from day one (teams have different metadata standards). This is handled via a JSON schema on experiment templates.
