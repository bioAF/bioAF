# bioAF Deployment Guide

## Prerequisites

- **Docker** and **Docker Compose** (v2 plugin)
- **Git**
- **openssl** (for secret generation during install)

That is it. bioAF runs entirely in Docker containers. No language runtimes,
package managers, or cloud CLIs are required on the host machine.

## Quick Deploy (All-in-One)

The fastest path from zero to a running instance:

```bash
git clone https://github.com/not-that-guy-again/bioAF.git
cd bioAF
./bioaf setup
```

`setup` handles everything automatically:

1. Checks prerequisites (Docker, Docker Compose, Git, openssl)
2. Generates `docker/.env` with secure random credentials
3. Generates self-signed TLS certificates
4. Removes any stale database volumes from a previous install
5. Builds all container images
6. Starts services in dependency order (db, backend, frontend, nginx)
7. Runs database migrations
8. Prints a one-time setup code and the access URL

Open the printed URL in your browser, enter the setup code, and follow the
wizard to create your admin account and configure the platform.

bioAF requires a Linux server (typically a GCP VM). Running `./bioaf setup`
on macOS or Windows will print instructions for setting up a GCP instance.

## Installer

`./install.sh` can also be run standalone for more control. `setup` calls
it automatically when needed.

- `./install.sh` -- interactive install (checks prereqs, generates env + certs)
- `./install.sh --non-interactive` -- accept all defaults, no prompts
- `./install.sh --force` -- regenerate secrets (breaks existing DB volume)
- `./install.sh check-prereqs` -- only check prerequisites
- `./install.sh generate-env` -- only generate env file
- `./install.sh generate-certs` -- only generate TLS certificates

## Architecture

```text
                :80 (redirect)
                :443 (HTTPS)
                      |
                    nginx
                   /     \
           /api/*          /*
             |              |
          backend        frontend
        (FastAPI)       (Next.js)
          :8000           :3000
             |
          PostgreSQL
           :5432
```

All four services run as Docker containers orchestrated by Docker Compose.
nginx handles TLS termination and request routing. HTTP on port 80 is
redirected to HTTPS on port 443.

## Service Management

```bash
./bioaf start               # Start all services
./bioaf stop                # Stop all services
./bioaf restart             # Stop then start
./bioaf status              # Show service status
./bioaf logs                # Tail all logs
./bioaf logs backend        # Tail logs for one service
```

## Database Operations

```bash
./bioaf migrate             # Run pending Alembic migrations
./bioaf backup              # Dump database to backups/ directory (gzipped)
./bioaf seed seed_poc_data.py  # Run a seed script inside the backend container
./bioaf dbshell             # Open a psql session
./bioaf reset-db            # Drop and recreate database (destructive, with confirmation)
```

## Debugging

```bash
./bioaf shell               # Open sh in backend container
./bioaf shell frontend      # Open sh in frontend container
./bioaf logs backend        # Tail backend logs
./bioaf dbshell             # Open psql
```

## Updating

```bash
./bioaf update
```

Pulls the latest code from the repository, rebuilds all container images,
and runs any new database migrations.

## Reinstalling

If you need to start fresh (e.g., after changing database credentials):

```bash
./bioaf stop
docker compose -f docker/docker-compose.yml down -v   # Remove containers and volumes
rm docker/.env docker/certs/tls.crt docker/certs/tls.key
./bioaf setup
```

Removing `docker/.env` and the certs causes `setup` to re-run the full
installer. The `down -v` removes the PostgreSQL data volume, which is
necessary because PostgreSQL bakes the initial password into the volume
on first startup.

## GCP VM Deployment

To deploy bioAF on a Google Cloud VM:

1. Create a VM (e2-medium or larger recommended):

   ```bash
   gcloud compute instances create bioaf \
     --zone=us-central1-a \
     --machine-type=e2-medium \
     --image-family=ubuntu-2204-lts \
     --image-project=ubuntu-os-cloud \
     --boot-disk-size=30GB \
     --tags=bioaf
   ```

2. Open ports 80 and 443 in the firewall:

   ```bash
   gcloud compute firewall-rules create bioaf-allow-web \
     --allow=tcp:80,tcp:443 \
     --target-tags=bioaf \
     --source-ranges=0.0.0.0/0
   ```

3. SSH into the VM and install Docker:

   ```bash
   gcloud compute ssh bioaf --zone=us-central1-a
   # Follow Docker's official install instructions for Ubuntu
   ```

4. Clone and deploy:

   ```bash
   git clone https://github.com/not-that-guy-again/bioAF.git
   cd bioAF
   ./bioaf setup
   ```

5. Access at `https://<VM_EXTERNAL_IP>`.

## Post-Deploy Configuration

### SMTP

For email notifications and user invitations, configure SMTP in
Admin > Settings after logging in.

### Component Activation

From the Components page, enable infrastructure components your team needs:
SLURM HPC, Filestore NFS, JupyterHub, RStudio, Nextflow, Snakemake,
cellxgene. Each component is provisioned via Terraform when enabled.

### Backup Configuration

Backups are configured with sensible defaults. Review and adjust retention
policies in Admin > Backup & Recovery.

## Teardown

Stop all services and remove Docker resources:

```bash
./bioaf stop
docker compose -f docker/docker-compose.yml down -v
```

All data in the PostgreSQL volume will be destroyed. Create a backup first
with `./bioaf backup` if needed.
