# ADR-012: Customer Owns All Data and Infrastructure State

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF is deployed into the customer's own GCP project. Unlike a SaaS platform, the customer's data never leaves their infrastructure. However, the degree to which the customer can operate independently of bioAF (e.g., if they decide to stop using the platform) depends on architectural choices about where data lives and in what format.

## Decision

All data, infrastructure state, and configuration are stored in resources owned by the customer in their GCP project. If a team decides to stop using bioAF, they retain full access to everything without needing bioAF's cooperation or any export process.

### What the Customer Owns

| Asset | Location | Format | Accessible Without bioAF |
|---|---|---|---|
| Raw sequencing data (FASTQs) | GCS bucket in customer's project | Standard FASTQ files | Yes: standard GCS access |
| Processed data (BAMs, h5ad, figures) | GCS bucket in customer's project | Standard bioinformatics formats | Yes: standard GCS access |
| Experiment tracking database | Cloud SQL in customer's project | PostgreSQL | Yes: standard Postgres tools (pg_dump, psql, any SQL client) |
| Audit log | Same Cloud SQL instance | PostgreSQL table | Yes: same as above |
| Infrastructure definitions | GitHub repo in customer's org | Terraform HCL + YAML | Yes: standard Terraform |
| Environment definitions | Same GitHub repo | Conda YAML, R package lists | Yes: standard conda/R tooling |
| Pipeline configurations | Same GitHub repo | Nextflow configs, JSON parameter files | Yes: standard Nextflow |
| Terraform state | GCS bucket in customer's project | Terraform state JSON | Yes: standard Terraform |
| Platform config backups | GCS bucket in customer's project | JSON files | Yes: human-readable JSON |

### Teardown Flow

`bioaf destroy --keep-data` removes the bioAF control plane and compute infrastructure while preserving:

- All GCS buckets (raw data, working data, results, config backups)
- Cloud SQL database (experiment tracking, audit log, user records)
- GitHub repository (infrastructure definitions, environment specs)
- Terraform state bucket

The customer retains full, standard-tooling access to all of the above.

### Structured Export (Deferred)

A one-click export feature (database dump as JSON/CSV, configuration archive) is deferred from v1. The rationale: since all data already lives in the customer's project in standard formats, a dedicated export feature is a convenience, not a necessity. It can be added later without architectural changes.

## Rationale

- **Trust and adoption.** Open-source infrastructure tools live or die by trust. If customers feel locked in, they won't adopt. Knowing they can walk away at any time: with all their data, in standard formats, removes the biggest adoption barrier.
- **No data sovereignty concerns.** Data never transits through bioAF infrastructure or any third party. It stays in the customer's GCP project from ingestion to analysis.
- **Standard formats everywhere.** FASTQs, BAMs, h5ad, PostgreSQL, Terraform, conda YAML: these are all industry-standard formats that work with thousands of other tools. bioAF adds value through orchestration and UI, not through proprietary formats.
- **GitOps repo as documentation.** Even if the customer never looks at the repo during normal use, it serves as complete documentation of their infrastructure if they ever need to operate without bioAF.

## Consequences

- bioAF must not introduce any proprietary data formats. All metadata is stored in PostgreSQL using standard types (JSONB for flexible fields, not custom binary formats).
- The GitOps repo must be self-documenting: the Terraform files should work with standard `terraform apply` even without the bioAF control plane (though they may need manual variable population).
- The `--keep-data` flag on `bioaf destroy` must be thoroughly tested. Destroying compute while preserving data is the critical path for customer trust.
- Documentation should include a "life after bioAF" section explaining how to access and use each preserved asset with standard tools.
