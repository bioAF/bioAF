<p align="center">
  <img src="assets/mascot.png" alt="bioAF" width="200" />
</p>

<h1 align="center">bioAF</h1>
<p align="center"><strong>Computational Biology Automation Framework</strong></p>

A turnkey computational biology platform for small biotech companies (5-50 researchers), deployed on Google Cloud Platform. bioAF provides a web-based control plane for managing HPC clusters, notebook environments, pipeline engines, and data visualization tools -- all provisioned through UI-driven Terraform.

## Features

- **Experiment Tracking** - MINSEQE-compliant metadata, sample management, batch processing, project organization
- **Compute Orchestration** - Kubernetes (GKE) or SLURM compute via the BioAF Adapter Layer, JupyterHub/RStudio notebooks, versioned compute environments, auto-scaling, Cloud Build image pipeline
- **Pipeline Engine** - Nextflow and Snakemake integration, pipeline catalog, run monitoring, parameter management
- **Data Management** - File upload/download, dataset browser, GCS storage integration, GEO export, SuperSeries cross-experiment packaging
- **Results & Visualization** - QC dashboards, cellxgene single-cell viewer, plot archive, search
- **SSH Access** - One-click kubectl exec into running pipeline jobs and notebook sessions
- **Notifications** - Event-driven alerts via in-app, email (SMTP), and Slack (OAuth integration)
- **Cost Center** - GCP billing integration, budget alerts, component cost breakdown, projections
- **Backup & Recovery** - 4-tier GCS backups (pg_dump, GCS versioning, platform config, terraform state), restore with review period
- **Session Credentials** - Per-user RStudio credentials with PAM authentication, auto-generated usernames
- **Role-Based Access** - Permission-based RBAC with four built-in roles, custom role creation, and per-resource/action grants
- **Upgrade System** - GitHub-based version checking, managed upgrade flow with rollback
- **Audit Log** - Immutable audit trail with filtering, pagination, and human-readable descriptions
- **GitOps** - Version-controlled platform configuration with diff and rollback

## Architecture

```text
                        +----------------------------+
                        |     Researcher's Browser   |
                        +----------------------------+
                                     |
                    +----------------+-----------------+
                    |        Frontend (Next.js 14)     |
                    |                                  |
                    |  Projects   Pipelines   Results  |
                    |  Workbench  Data & Files  Infra  |
                    +-----------------+----------------+
                                      |
                    +-----------------+-----------------+
                    |        Backend (FastAPI)          |
                    |                                   |
                    |  Experiment     Pipeline    File  |
                    |  Service        Orchestrator Mgr  |
                    |                                   |
                    |  Event Bus  Notifications  Audit  |
                    +---------+----------+--------------+
                              |          |
               +--------------+    +-----+----------+
               |                   |  PostgreSQL 16 |
               |                   |  (Cloud SQL)   |
               |                   +----------------+
               |
    +----------+-----------+
    | BioAF Adapter Layer  |
    |       (BAL)          |
    |                      |
    |  Compute   Storage   |
    |  Provider  Provider  |
    |  Notebook Provider   |
    +--+-------+-------+---+
       |       |       |
       v       v       v
  +---------+-----+---------+              +--------------------+
  |     GKE Autopilot       |              |  SLURM + NFS       |
  |  +---------+---------+  |              |  (coming soon)     |
  |  |Pipelines|Notebooks|  |              +--------------------+
  |  |Node Pool|Node Pool|  |
  |  +---------+---------+  |
  +----------+--+-----------+
             |  |
  +----------+  +----------+
  |                        |
  v                        v
+----------+     +---------+---------+---------+---------+
| Secret   |     |              GCS Buckets              |
| Manager  |     |  ingest/  raw/  working/  results/    |
+----------+     +---------------------------------------+

                         Data Flow
                         --------

  FASTQs from sequencer         Pipeline results
        |                              ^
        v                              |
  [ bioaf-ingest ]  -->  [ bioaf-raw ]  -->  [ bioaf-working ]  -->  [ bioaf-results ]
    auto-ingest          permanent           intermediate             final outputs
    (Pub/Sub)            storage             (TTL 30 days)            h5ad, plots, QC
```

### How it works

A computational biologist registers an experiment, links FASTQ files (uploaded or auto-ingested from a sequencer drop), selects a pipeline from the catalog (nf-core/scrnaseq, rnaseq, or custom), and launches a run. The **BioAF Adapter Layer** handles everything below that: staging inputs from GCS, submitting Kubernetes Jobs to GKE Autopilot, monitoring execution via Nextflow trace parsing, collecting outputs back to GCS, and transitioning the experiment through its status lifecycle (`registered` -> `library_prep` -> `sequencing` -> `fastq_uploaded` -> `processing` -> `pipeline_complete` -> [`reviewed` ->] `analysis` -> `complete`). Pipeline completion triggers event-driven notifications (in-app, email, Slack), and results are browsable through the plot archive, cellxgene viewer, and GEO export tools. Jupyter and RStudio sessions run as Kubernetes Pods with GCS-backed home directories and SSH access. RStudio sessions use per-user PAM authentication ([ADR-030](decisions/ADR-030-session-credentials-pam-auth.md)), and notebook container images are managed as versioned environments ([ADR-033](decisions/ADR-033-versioned-compute-environments.md)), built automatically via Cloud Build ([ADR-031](decisions/ADR-031-notebook-image-build-pipeline.md)).

The adapter layer ([ADR-020](decisions/ADR-020-bioaf-adapter-layer.md)) abstracts compute, storage, and notebook providers behind clean interfaces, so all application logic is decoupled from infrastructure specifics. Today that means GKE + GCS ([ADR-021](decisions/ADR-021-kubernetes-compute-backend.md), [ADR-022](decisions/ADR-022-gcs-storage-backend.md)); SLURM + NFS is stubbed for teams that need traditional HPC.

Infrastructure is provisioned through UI-driven Terraform ([ADR-007](decisions/ADR-007-ui-driven-terraform.md)) -- researchers never touch HCL. All secrets live in Secret Manager ([ADR-008](decisions/ADR-008-secret-manager.md)), all actions are recorded in an immutable audit log ([ADR-009](decisions/ADR-009-immutable-audit-log.md)), and data portability is guaranteed ([ADR-012](decisions/ADR-012-data-portability.md)).

See all architecture decision records in [decisions/README.md](decisions/README.md).

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git
- openssl (for secret generation)

### Deploy

```bash
# Clone the repository
git clone https://github.com/not-that-guy-again/bioAF.git
cd bioAF

# Option A: All-in-one setup (generates config, builds, migrates, creates admin)
./bioaf setup

# Option B: Step-by-step
./install.sh                    # Check prerequisites and generate docker/.env
./bioaf build                   # Build container images
./bioaf start                   # Start all services
./bioaf migrate                 # Run database migrations
./bioaf create-admin            # Create your admin account
```

The `setup` command prompts for your organization name, admin email, and
password. It generates all secrets, builds containers, runs database
migrations, and creates the admin account automatically. When setup
completes, it prints the access URL for your instance.

For step-by-step installs, `install.sh` checks that prerequisites are
installed and generates `docker/.env` with auto-generated database
credentials and secret keys. Run `./install.sh --help` for options
including `--non-interactive` and `--force`.

### Management Commands

| Command | Description |
| ------- | ----------- |
| `./bioaf setup` | Interactive first-run setup (all-in-one) |
| `./bioaf start` | Start all services in dependency order |
| `./bioaf stop` | Stop all services |
| `./bioaf restart` | Restart all services |
| `./bioaf status` | Show service status |
| `./bioaf logs [service]` | Tail logs (all or one service) |
| `./bioaf build [service]` | Build (or rebuild) container images |
| `./bioaf migrate` | Run database migrations |
| `./bioaf create-admin` | Create an admin user account |
| `./bioaf seed <script.py>` | Run a seed/data script in the backend container |
| `./bioaf backup` | Create a database backup |
| `./bioaf update` | Pull latest code, rebuild, and migrate |
| `./bioaf reset-db` | Destroy and recreate the database (with confirmation) |
| `./bioaf shell [service]` | Open a shell in a container (default: backend) |
| `./bioaf dbshell` | Open a psql session to the database |
| `./bioaf help` | Show all commands |

See the full [Deployment Guide](docs/deployment-guide.md) for detailed instructions.

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
  backend/           FastAPI application
  frontend/          Next.js 14 application
  docker/            Dockerfiles, compose, and nginx config
  terraform/         GCP infrastructure as code
  helm/              Kubernetes deployment chart
  decisions/         Architecture Decision Records
  documentation/     Product and architecture specs
  docs/              User-facing documentation
  scripts/           Database seed and utility scripts
  tests/shell/       BATS tests for install.sh and bioaf scripts
  bioaf              Management script (entry point)
  install.sh         First-time installer (prereq checks + env generation)
```

## Contributing

See the ADRs in [decisions/](decisions/) for architectural context before making changes. All infrastructure changes must go through the UI-driven Terraform workflow (ADR-007). The audit log is immutable by design (ADR-009).
