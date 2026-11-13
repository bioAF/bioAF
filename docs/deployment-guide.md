# bioAF Deployment Guide

## Prerequisites

- **Docker** and **Docker Compose** (v2 plugin)
- **Git**
- **openssl** (for secret generation during install)

That is it. bioAF runs entirely in Docker containers. No language runtimes,
package managers, or cloud CLIs are required on the host machine.

## One-Command GCP Setup

The fastest path from zero to a running instance on Google Cloud. Run this
on your local machine (macOS or Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/bioAF/bioAF/main/install-gcp.sh | bash
```

The script walks you through:

1. Installing the gcloud CLI (if not present). On a Mac it installs it with
   Homebrew, installing Homebrew first if needed; that asks for your Mac password.
2. Authenticating with Google Cloud
3. Selecting your GCP project
4. Enabling required APIs (Compute, Storage, IAM)
5. Creating a firewall rule for ports 80 and 443
6. Creating an e2-medium VM with Ubuntu 22.04 and Docker pre-installed
7. Optionally creating a service account with a JSON key

Once the VM is ready, SSH in and deploy:

```bash
gcloud compute ssh bioaf --zone=<your-zone> --project=<your-project>
git clone https://github.com/bioAF/bioAF.git
cd bioAF
./bioaf setup
```

Open the printed URL in your browser, enter the setup code, and follow the
wizard to create your admin account and configure the platform.

## One-Command AWS Setup

To deploy on AWS instead of GCP, use the sibling installer. Run it on your
local machine (macOS or Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/bioAF/bioAF/main/install-aws.sh | bash
```

The script walks you through:

1. Installing the AWS CLI v2 (if not present). On a Mac it installs it with
   Homebrew, installing Homebrew first if needed; that asks for your Mac password.
2. Verifying your AWS credentials (`aws sts get-caller-identity`)
3. Selecting a region
4. Creating an EC2 key pair for SSH (private key saved locally)
5. Creating a security group (ports 80 and 443 from anywhere, port 22 from you)
6. Creating the `bioaf-app` IAM role + instance profile (starter policy)
7. Launching an Ubuntu 22.04 EC2 VM (IMDSv2-only) with the profile attached
8. Stamping the install as `cloud_provider: aws`

The VM authenticates to AWS through its instance profile (the analog of the GCP
VM's attached service account), so no long-lived credentials are stored. The
script then offers to finish setup on the VM for you, or prints a worksheet so
you can SSH in and run `./bioaf setup` yourself.

Pass options with the cloned form, e.g. `./install-aws.sh --region us-east-1`
or `./install-aws.sh --help`. From there, deployment is identical to GCP: open
the printed URL, enter the setup code, and follow the wizard. Storage (S3) and
Kubernetes compute (EKS) are provisioned from the Infrastructure page once you
are in; a few components are still being brought to parity with GCP.

## Quick Deploy (Existing Server)

If you already have a Linux server with Docker installed:

```bash
git clone https://github.com/bioAF/bioAF.git
cd bioAF
./bioaf setup
```

`setup` handles everything automatically:

1. Checks prerequisites (Docker, Docker Compose, Git, openssl)
2. Generates `docker/.env` with secure random credentials
3. Generates self-signed TLS certificates
4. Removes any stale database volumes from a previous install
5. Pulls pre-built images from GitHub Container Registry
6. Starts services in dependency order (db, backend, frontend, nginx)
7. Runs database migrations
8. Prints a one-time setup code and the access URL

bioAF requires a Linux server. Running `./bioaf setup` on macOS or Windows
will print instructions for setting up a GCP instance.

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
./bioaf migrate             # Upgrade the database (verified backup first); retry after a failure
./bioaf migrate-down <rev>  # Downgrade to a specific revision (also backs up first)
./bioaf backup              # Dump database to backups/ directory (gzipped, verified)
./bioaf seed <script.py>       # Run a seed script inside the backend container
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

From the CLI:

```bash
./bioaf update              # Update to the latest release
./bioaf update 0.7.0        # Update to a specific version
```

This backs up the database, fetches the target version tag from GitHub,
pulls pre-built images, checks the new backend's configuration, restarts
services, upgrades the database, and waits until the backend reports ready.
The new release's image tag is written to `docker/.env` only after its
configuration check passes, so a rejected check leaves the current release
pinned and running.

From the UI, admins can click the **Install Update** button on the
**Settings > Information** page when a new version is available. This
triggers the same update process through a host-side update agent.

### How the database is upgraded

Every way of upgrading the database (the backend starting a new version,
`./bioaf update`, `./bioaf migrate`, the update agent, and the update
command of any older release) goes through the same steps in the backend:

1. **One at a time.** A database lock makes a second attempt wait, then find
   nothing left to do.
2. **Recovery backup first.** Before anything changes, the backend writes a
   PostgreSQL 16 dump to `backups/` on the host
   (`bioaf_pre_upgrade_<from>_to_<to>_<time>.dump`, with a `.json` manifest
   naming the database revision, tool versions and checksum). It is kept only
   if `pg_dump` succeeded and the dump lists data for every table. Each dump
   holds the whole database, so `backups/` is private (0700) and every bioAF
   backup in it is 0600, owned by the account that owns `backups/` (the
   operator who runs `./bioaf`), so that account can restore without root. If the
   backup cannot be made (no space, directory not writable, dump unreadable),
   **nothing is changed** and the update stops with the reason. Free space
   needed: about 1.5 times the database size plus 512 MiB.
3. **Upgrade and check**, then record the result in
   `update-status/schema-upgrade.json`.

Older data is repaired automatically on the way; no manual SQL is needed.
One visible case: before release v0.15.0 a sample's ID did not have to be
unique within an experiment. If yours had repeats, each of those samples now
has its own ID (`<old ID>~<sample number>`), the old ID is shown beside it as
"before upgrade", and looking up or creating a sample by the repeated old ID
answers with the candidates instead of choosing one.

Until the upgrade has completed, the backend answers every request with a
"finishing an upgrade" message and does no background work. The web app
shows that message and picks up by itself when the backend is ready.

### Compute mode

`BIOAF_COMPUTE_MODE` decides whether the compute, notebook, storage and
work-node adapters simulate their work or contact their real backends. It does
not choose the cloud or the compute stack (those come from the platform
configuration).

| Value | Meaning |
| --- | --- |
| `kubernetes` (alias `k8s`) | Real backends. The bundled Compose file supplies it when `docker/.env` leaves the mode unset or empty. |
| `local` | Simulation, for development. Not a full offline mode: cellxgene, for example, has no simulation. |
| `slurm` | Legacy value, treated as real backends. It does not select the SLURM stack or start SLURM polling. |

Case and surrounding spaces are ignored. Any other value, or an empty value
given to the backend directly, stops it at startup with a message naming the
setting. Each start logs the effective value:
`Compute configuration: mode=kubernetes simulation=disabled active_compute_stack=kubernetes`
(`./bioaf logs backend`). The mode and the active stack are read once at
startup; a change applies after the backend is recreated.

Outside the bundled Compose file (custom launchers, `uvicorn` by hand, Alembic
or CLI commands), the setting has no default and must be supplied. The host's
`docker/.env` is mounted at `/host/.env`; the backend does not read it as its
own `.env`.

#### If the backend rejects its configuration

The backend checks its settings before touching the database, and `./bioaf
update` and `./bioaf migrate` check them in a one-off container first. A
configuration problem is reported as one: the database is not changed and no
backup is needed. Correct the value in `docker/.env` (or wherever your
launcher sets it, and make sure an exported shell variable is not overriding
the file), then:

- **During an update** (status step `settings_preflight`): run the same
  `./bioaf update <version>` again. It recreates both the backend and the
  frontend. Do not recreate only the backend to resume an update: the frontend
  could stay on the old release while the updater considers the update done.
  The current release keeps running until the check passes, so no image
  rollback is needed.
- **Otherwise** (`./bioaf migrate`, a manual start): recreate the backend so
  it receives the new value (a restart keeps the old one), from the
  repository root:
  `docker compose -f docker/docker-compose.yml --env-file docker/.env up -d backend`

An updater from an older release that is already running when it checks out
this one keeps its old messages: it can pin the new release, start a backend
that rejects its mode, and report a database upgrade failure. Correct the
mode and run `./bioaf update <version>` again; the new updater checks the
settings, backs up, applies the corrected configuration to the services and
then completes the upgrade. A release older than this check has no checker;
`./bioaf update` and `./bioaf migrate` then report that the configuration could
not be checked (not that it is invalid). Use that release's own `./bioaf` for
it.

#### Configuration-only rollback

To return to the previous release after a configuration failure outside an
update, keep the database, the volumes and `docker/.env`'s encryption keys as
they are. Set `BIOAF_IMAGE_TAG` in `docker/.env` to the previous release's tag,
including its `v` (for example `BIOAF_IMAGE_TAG=v2026.11.20`), make sure no
exported `BIOAF_IMAGE_TAG` overrides it, and recreate only the backend:

```bash
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --no-deps --force-recreate backend
```

This change adds no database migration, so no restore, reset or Alembic
downgrade is involved. Both the older `${BIOAF_COMPUTE_MODE:-k8s}` and the
current `${BIOAF_COMPUTE_MODE:-kubernetes}` Compose spellings give either
release a valid mode. Do not follow the database-restore steps below for a
configuration failure.

### If an update fails

1. Read `update-status/schema-upgrade.json` (and `./bioaf logs backend`).
   It names the step that failed (`backup`, `migrate`, `validate`) and why.
   If the update stopped at `settings_preflight` (Checking backend
   configuration), the database was not touched: see
   [If the backend rejects its configuration](#if-the-backend-rejects-its-configuration).
2. Fix the cause (for example free disk space), then retry with
   `./bioaf migrate` or `./bioaf update <version>`. Retrying works when the
   new version's images are already installed; a completed upgrade is never
   repeated and earlier backups are never overwritten.
3. To return to the state before the update instead, you need three things
   from the same moment: the dump in `backups/`, `docker/.env` (its
   `BIOAF_ENCRYPTION_KEYS` decrypts the stored secrets; the manifest lists
   fingerprints of the keys that were in use), and the release the dump came
   from. Check out that release's tag, set `BIOAF_IMAGE_TAG` in `docker/.env`
   to it, and restore into a fresh database:

   ```bash
   ./bioaf stop
   docker compose -f docker/docker-compose.yml --env-file docker/.env up -d db
   docker compose -f docker/docker-compose.yml --env-file docker/.env exec -T db \
       sh -c 'dropdb -U bioaf bioaf && createdb -U bioaf bioaf'
   docker compose -f docker/docker-compose.yml --env-file docker/.env exec -T db \
       pg_restore -U bioaf -d bioaf --no-owner < backups/bioaf_pre_upgrade_<...>.dump
   ./bioaf start
   ```

   A gzipped SQL backup from `./bioaf backup` or an older update restores the
   same way with `gunzip -c backups/<file>.sql.gz | ... exec -T db psql -U bioaf -d bioaf`.
   Alembic downgrades are not a substitute: they do not undo data repairs.

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

## Manual GCP VM Deployment

If you prefer to set up the GCP infrastructure manually instead of using
`install-gcp.sh`:

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
   git clone https://github.com/bioAF/bioAF.git
   cd bioAF
   ./bioaf setup
   ```

5. Access at `https://<VM_EXTERNAL_IP>`.

## Post-Deploy Configuration

### SMTP

For email notifications and user invitations, configure SMTP in
Settings > Integrations after logging in.

### Component Activation

From the Components page, enable infrastructure components your team needs:
SLURM HPC, Filestore NFS, JupyterHub, RStudio, Nextflow, Snakemake,
cellxgene. Each component is provisioned via Terraform when enabled.

### Backup Configuration

Backups are configured with sensible defaults. Review and adjust retention
policies in Infrastructure > Backup & Recovery.

## Teardown

Stop all services and remove Docker resources:

```bash
./bioaf stop
docker compose -f docker/docker-compose.yml down -v
```

All data in the PostgreSQL volume will be destroyed. Create a backup first
with `./bioaf backup` if needed.
