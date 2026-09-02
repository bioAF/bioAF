# bioAF Documentation

Welcome to the bioAF documentation. bioAF is a turnkey computational biology platform for small biotech companies, deployed on Google Cloud Platform.

## Quick Links

- [Quick Start Guide](../README.md#quick-start) - Deploy in 30 minutes
- [Deployment Guide](deployment-guide.md) - Full deployment walkthrough
- [Life After bioAF](life-after-bioaf.md) - Data portability and asset access

## User Guides

- [Bench Scientist Guide](user-guide-bench.md) - Experiment registration, sample management, QC results
- [Computational Biologist Guide](user-guide-compbio.md) - Pipelines, notebooks, environments, data management
- [Admin Guide](user-guide-admin.md) - User management, components, costs, backups, notifications
- [Literature Validation](guides/literature-validation.md) - Triage whether a paper is worth a scientist's further review, by reproducing its analysis

## Architecture

- [ADR Index](adr-index.md) - All Architecture Decision Records
- [Architecture Spec](../documentation/bioAF-architecture-spec-v0_3.md) - System architecture
- [Product Spec](../documentation/bioAF-product-spec-v0_5.md) - Full product specification

## API Reference

The bioAF Integration API is the public, key-authenticated REST surface for
LIMS and other external integrations.

- [Integration API overview](api/README.md)
- [Authentication and Authorization](api/auth.md)
- [Conventions](api/conventions.md): error envelope, idempotency, pagination, status codes
- Per-resource contracts: [Projects](api/projects.md), [Experiments](api/experiments.md), [Samples](api/samples.md), [Files](api/files.md)
- [Webhooks](api/webhooks.md): event catalog, signature, retry/dead-letter

The authoritative schema is the live OpenAPI document served at
`/api/v1/integrations/openapi.json` (Swagger UI at
`/api/v1/integrations/docs`). The internal/admin API (under `/api/...`,
JWT-authenticated) auto-generates an OpenAPI spec at `/docs` when
`BIOAF_ENVIRONMENT=development`; it is disabled in production.
