# bioAF Documentation

bioAF combines research records, analysis tools and cloud infrastructure management. GCP is the reference
deployment; AWS implementations cover several workload paths but full platform parity is incomplete.
See [architecture and support status](architecture.md) before choosing a deployment.

## Start Here

- [Quick Start](../README.md#quick-start): installation entry points.
- [Deployment Guide](deployment-guide.md): install, update, recover and remove an installation.
- [Configuration](configuration.md): settings sources, compute mode, logging and operational limits.
- [Life After bioAF](life-after-bioaf.md): preserve data and infrastructure records before teardown.
- [Installation Metrics](installation-metrics.md): the optional, opt-in reports an installation can share,
  and how to change or withdraw that choice.

## Operator Procedures

- [Foundation Bucket Guards](operations/foundation-bucket-guards.md): add public-access prevention to
  an existing GCP installation's Terraform-state and backup buckets.
- [EKS API Endpoint and Control-Plane Logs](operations/eks-endpoint-and-logs.md): endpoint modes,
  the reviewed cutover and its recovery, and log retention and readers on AWS installations.

## Role Guides

Built-in roles are starting points. Actual actions depend on effective permissions and enabled components.

- [Bench Scientist Guide](user-guide-bench.md)
- [Computational Biologist Guide](user-guide-compbio.md)
- [Admin Guide](user-guide-admin.md)

## Workflow Guides

| Guide | Covers |
| --- | --- |
| [Compute Stack Setup](guides/compute-stack-setup.md) | Kubernetes, cloud status, Work Nodes and simulation |
| [SSH and Exec Access](guides/ssh-access.md) | Generated connection commands, prerequisites and audit limits |
| [Custom Pipelines](guides/custom-pipelines.md) | Code, variables, environments and output contracts |
| [Custom QC Configuration](guides/custom-qc-config.md) | Metrics and dashboard sections |
| [Automated Pipelines](guides/automated-pipelines.md) | Experiment auto-run configuration and current ingest limitations |
| [Cross-Experiment Projects](guides/cross-experiment-projects.md) | Membership, scope and provenance |
| [Reference Data](guides/reference-data.md) | Upload, URL import, versions and deprecation |
| [Naming Profiles](guides/naming-profiles.md) | Parse-only filename rules |
| [Analysis Snapshots](guides/analysis-snapshots.md) | Python and R SDK examples |
| [Data Exports](guides/data-exports.md) | File archives, size limits and signed links |
| [GEO Export](guides/geo-export.md) | Metadata packages and SuperSeries |
| [Literature and AI Workflows](guides/literature-and-ai.md) | Search, reviews, provider settings and assistant approval |
| [Paper Validation](guides/paper-validation.md) | Beta reproduction studies and evidence limits |

## Architecture and Development

- [Current architecture](architecture.md), with the [SVG diagram](../assets/bioAF_Architecture.svg).
- [Development Guide](development.md): reproducible setup, tests and shared implementation contracts.
- [Contributing](../CONTRIBUTING.md) and [AI Guides](../ai_guides/README.md).
- [Architecture Decision Records](../decisions/README.md): historical decisions and their recorded statuses.
- [Backend dependencies](../backend/DEPENDENCIES.md), [frontend dependencies](../frontend/DEPENDENCIES.md),
  and [internal API contracts](../backend/contracts/README.md).

## API Reference

The public Integration API uses service accounts and scoped API keys under `/api/v1/integrations`.

- [Overview](api/README.md)
- [Authentication and Authorization](api/auth.md)
- [Conventions](api/conventions.md)
- [Projects](api/projects.md), [Experiments](api/experiments.md), [Samples](api/samples.md), [Files](api/files.md)
- [Webhooks](api/webhooks.md)

Its authoritative schema is `/api/v1/integrations/openapi.json`, and its Swagger UI is
`/api/v1/integrations/docs`. The internal application API has a separate committed OpenAPI artifact.
Internal `/docs` and `/openapi.json` are available only with `BIOAF_ENVIRONMENT=development`.
Internal experiment pagination uses `page`/`page_size`; Integration API pagination uses cursors.
