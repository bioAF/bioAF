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

## Architecture

- [ADR Index](adr-index.md) - All Architecture Decision Records
- [Architecture Spec](../documentation/bioAF-architecture-spec-v0_2.md) - System architecture
- [Product Spec](../documentation/bioAF-product-spec-v0_4.md) - Full product specification

## API Reference

The FastAPI backend auto-generates an OpenAPI specification at `/docs` when running. Access it at `http://localhost:8000/docs` during development.
