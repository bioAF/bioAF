<p align="center">
  <img src="assets/mascot.png" alt="bioAF" width="200" />
</p>

<h1 align="center">bioAF</h1>
<p align="center"><strong>Bioinformatics Application Framework</strong></p>

A turnkey computational biology platform for small biotech companies (5-50 researchers), deployed on Google Cloud Platform. bioAF provides a web-based control plane for managing HPC clusters, notebook environments, pipeline engines, and data visualization tools -- all provisioned through UI-driven Terraform.

## Features

- **Experiment Tracking** - MINSEQE-compliant metadata, sample management, batch processing, project organization
- **Compute Orchestration** - Kubernetes (GKE) or SLURM compute via the BioAF Adapter Layer, JupyterHub/RStudio notebooks, auto-scaling, quota management
- **Pipeline Engine** - Nextflow and Snakemake integration, pipeline catalog, run monitoring, parameter management
- **Data Management** - File upload/download, dataset browser, GCS storage integration, GEO export, SuperSeries cross-experiment packaging
- **Results & Visualization** - QC dashboards, cellxgene single-cell viewer, plot archive, search
- **SSH Access** - One-click kubectl exec into running pipeline jobs and notebook sessions
- **Notifications** - Event-driven alerts via in-app, email (SMTP), and Slack webhooks
- **Cost Center** - GCP billing integration, budget alerts, component cost breakdown, projections
- **Backup & Recovery** - Tiered backups (Cloud SQL PITR, Filestore snapshots, config exports), one-click restore
- **Role-Based Access** - Four roles (admin, comp_bio, bench, viewer) with granular permissions
- **Upgrade System** - GitHub-based version checking, managed upgrade flow with rollback
- **Activity Feed & Access Logs** - Full audit trail and team activity tracking
- **GitOps** - Version-controlled platform configuration with diff and rollback

## Architecture Overview

```text
Frontend (Next.js 14)  -->  Backend (FastAPI)  -->  Cloud SQL PostgreSQL 16
                                |
                    BioAF Adapter Layer (BAL)
                       /              \
              Kubernetes + GCS     SLURM + NFS
              (recommended)        (coming soon)
                                |
                         Terraform Runner
                                |
                    GCP Infrastructure
                    (GKE, GCS, Secret Manager, Cloud NAT)
```

**Key design decisions:**

- GCP-only infrastructure ([ADR-001](decisions/ADR-001-gcp-only.md))
- Email-based authentication ([ADR-003](decisions/ADR-003-email-based-auth.md))
- Tiered backup strategy ([ADR-004](decisions/ADR-004-tiered-backup-strategy.md))
- UI-driven Terraform -- users never touch HCL ([ADR-007](decisions/ADR-007-ui-driven-terraform.md))
- All secrets in Secret Manager ([ADR-008](decisions/ADR-008-secret-manager.md))
- Immutable audit log ([ADR-009](decisions/ADR-009-immutable-audit-log.md))
- Event-driven notifications ([ADR-010](decisions/ADR-010-notification-system.md))
- Data portability guarantees ([ADR-012](decisions/ADR-012-data-portability.md))
- BioAF Adapter Layer for compute/storage abstraction ([ADR-020](decisions/ADR-020-bioaf-adapter-layer.md))
- Kubernetes compute backend ([ADR-021](decisions/ADR-021-kubernetes-compute-backend.md))
- GCS storage backend ([ADR-022](decisions/ADR-022-gcs-storage-backend.md))
- SSH access for running workloads ([ADR-026](decisions/ADR-026-ssh-access.md))
- Navigation restructure ([ADR-027](decisions/ADR-027-navigation-restructure.md))

See all ADRs in [decisions/README.md](decisions/README.md).

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

For GCP infrastructure provisioning, you also need:

- Google Cloud SDK (`gcloud`) authenticated
- Terraform >= 1.7

### Deploy

```bash
# Clone the repository
git clone https://github.com/not-that-guy-again/bioAF.git
cd bioAF

# Run the interactive setup (generates config, starts services, creates admin)
./bioaf setup

# Open http://localhost:8080 and log in with your admin credentials
```

The `setup` command prompts for your organization name, admin email, and
password. It generates all secrets, builds containers, runs database
migrations, and creates the admin account automatically.

### Management Commands

| Command | Description |
| ------- | ----------- |
| `./bioaf setup` | Interactive first-run setup |
| `./bioaf start` | Start all services |
| `./bioaf stop` | Stop all services |
| `./bioaf restart` | Restart all services |
| `./bioaf status` | Show service status |
| `./bioaf logs [service]` | Tail logs (all or one service) |
| `./bioaf migrate` | Run database migrations |
| `./bioaf backup` | Create a database backup |
| `./bioaf update` | Pull latest code, rebuild, and migrate |
| `./bioaf reset-db` | Destroy and recreate the database |
| `./bioaf help` | Show all commands |

See the full [Deployment Guide](docs/deployment-guide.md) for detailed instructions.

For VM provisioning on GCP, see the
[bioaf-deploy-demo](https://github.com/not-that-guy-again/bioaf-deploy-demo)
repository for Terraform modules.

## Documentation

- [Quickstart](docs/README.md) - Documentation hub
- [Deployment Guide](docs/deployment-guide.md) - Full deployment walkthrough
- [Bench Scientist Guide](docs/user-guide-bench.md) - Experiments, samples, results
- [Computational Biologist Guide](docs/user-guide-compbio.md) - Pipelines, notebooks, environments
- [Admin Guide](docs/user-guide-admin.md) - User management, costs, backups, notifications
- [Life After bioAF](docs/life-after-bioaf.md) - Data portability after teardown
- [ADR Index](decisions/README.md) - Architecture Decision Records
- [SSH Access Guide](docs/guides/ssh-access.md) - Connecting to running workloads
- [GEO Export Guide](docs/guides/geo-export.md) - Exporting to NCBI GEO
- [Reference Data Guide](docs/guides/reference-data.md) - Managing reference genomes and annotations
- [Compute Stack Setup](docs/guides/compute-stack-setup.md) - Kubernetes and SLURM configuration

## Development Setup

### Using Docker Compose (recommended)

```bash
# Start backend, frontend, and PostgreSQL
docker compose -f docker/docker-compose.dev.yml up

# Backend:  http://localhost:8000
# Frontend: http://localhost:3000
# Postgres: localhost:5432
```

### Manual Setup

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev

# Database (requires PostgreSQL 16)
cd backend
alembic upgrade head
```

### Running Tests

```bash
# Backend tests (requires PostgreSQL)
docker compose -f docker/docker-compose.dev.yml up -d db
cd backend && python -m pytest tests/ -v

# Frontend tests
cd frontend && npm test
```

## Component Catalog

bioAF manages these infrastructure components through its UI:

| Component | Category | Compute Stack | Dependencies |
| --------- | -------- | ------------- | ----------- |
| GKE Cluster | Compute | Kubernetes | None |
| GCS Buckets | Storage | Kubernetes | GKE |
| SLURM HPC Cluster | Compute | SLURM (coming soon) | None |
| Filestore NFS | Storage | SLURM (coming soon) | SLURM |
| JupyterHub | Notebooks | Both | Compute, Storage |
| RStudio Server | Notebooks | Both | Compute, Storage |
| Nextflow | Pipelines | Both | Compute |
| Snakemake | Pipelines | Both | Compute |
| cellxgene | Visualization | Any | None |
| QC Dashboard | Visualization | Any | None |
| Meilisearch | Search | Any | None |

## Project Structure

```text
bioAF/
  backend/        FastAPI application
  frontend/       Next.js 14 application
  terraform/      GCP infrastructure as code
  helm/           Kubernetes deployment chart
  cli/            Python CLI (bioaf deploy/destroy)
  docker/         Dockerfiles and compose
  decisions/      Architecture Decision Records
  documentation/  Product and architecture specs
  docs/           User-facing documentation
  scripts/        Database and utility scripts
```

## Contributing

See the ADRs in [decisions/](decisions/) for architectural context before making changes. All infrastructure changes must go through the UI-driven Terraform workflow (ADR-007). The audit log is immutable by design (ADR-009).
