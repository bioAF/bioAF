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

## Logging

### What the backend writes

The backend writes each log record as one JSON object on one line of stdout: `time` (UTC),
`severity` (`DEBUG` to `CRITICAL`), `logger` (`bioaf` or `bioaf.<module>`), `message`, and, when
present, `exception` (the whole traceback, chained causes included), `stack`, and `fields`
(structured data). Newlines, control characters and Unicode line separators are escaped inside the
line, so a traceback or a hostile request path can never start a forged record. Production logs
`INFO` and above; `BIOAF_DEBUG=true` adds `DEBUG`. There is no text format and no switching on
environment or terminal.

### Choosing the delivery path

`BIOAF_LOG_DELIVERY` selects exactly one centralized path. There is no automatic fallback between
them, including during a collector outage.

| Value | Who ships to Cloud Logging | Use it when |
| --- | --- | --- |
| `stdout` (default, both Compose files) | The Ops Agent on the VM reads Docker's log files | Supported GCE installs (`./bioaf setup` installs and configures the agent) |
| `direct` | The backend itself, with the GCP project and credentials in platform config | Nothing collects container output (no agent). Never together with the agent, or every backend record is ingested twice |

`./bioaf setup`, `start` and `update` read the value Compose will give the backend (from
`docker compose config`, so `docker/.env` and your shell environment both count). In `direct` mode
they install and reconfigure no agent, and they stop with an error if an Ops Agent on the host
already collects container logs; remove that collection or use `stdout`.

In `stdout` mode the backend builds no Cloud Logging client and reads no credentials for logging.
In `direct` mode stdout is still written; a direct sink that cannot be built is reported once by
error class and the backend keeps running with stdout only.

Installs from before this change attached the direct handler automatically on GCE while the agent
also collected stdout, so backend records reached Cloud Logging twice. After updating they arrive
once, through the agent. A manual install that relied on the automatic direct handler and runs no
collector must set `BIOAF_LOG_DELIVERY=direct` in `docker/.env`.

### The Ops Agent config

`docker/ops-agent-config.yaml` (schema v2) parses the backend's JSON out of Docker's `log` field, so
`severity`, `logger`, `message` and `fields` become entry fields and the record's own time becomes
the entry timestamp. Lines that are not JSON (other containers, Uvicorn's own stderr) are kept as
Docker's envelope.

`./bioaf setup`, `./bioaf start` and `./bioaf update` (including a retried update) reconcile
`/etc/google-cloud-ops-agent/config.yaml` before the services start:

- The installed file starts with `# bioaf-managed: ops-agent-config/v2 sha256=...`. A current file is
  left alone (no restart).
- The config earlier releases installed, or an unedited managed copy of any version, is replaced:
  the candidate is validated with the agent's own config generator, installed atomically, the agent
  is restarted, and its log collector must come back. If it does not, the previous file is restored
  and the agent restarted on it. Collection is never disabled as a fallback.
- Any other file (your own config, or a managed copy edited after install) is left unchanged and
  the command fails. The candidate is saved to `host-only/ops-agent-config-v2.candidate` (mode 600;
  this directory is never mounted into the backend). Merge it, or add the line
  `# bioaf-managed: operator` to your file to keep managing it yourself (it must then contain the
  `parse_bioaf_json` and `bioaf_severity` processors).
- Validation and restart output goes to the terminal when there is one, otherwise to
  `host-only/ops-agent-config-v2.log`. The update status file and the update agent's log only get a
  fixed message.

A failed reconciliation stops `./bioaf update` before the services restart (the status step is
`cloud_logging`) and stops `./bioaf start` before starting services. Run `./bioaf start` in a
terminal to see the cause.

An update started from an older release runs that release's updater, which does not know about
this config. Every updater since v0.13.1 runs the new release's `install.sh ensure-encryption-key`
after checking it out and before restarting the services; that step now reconciles the agent config
too, so the parser is in place before the new backend starts. If it fails, the older updater stops
with the step `ensure_encryption_key` and the message "encryption key bootstrap failed", leaving the
old services running. Run `./bioaf start` in a terminal to see the cause.

### Checking the effective path

```bash
grep -m1 bioaf-managed /etc/google-cloud-ops-agent/config.yaml   # managed version on this host
systemctl is-active google-cloud-ops-agent-fluent-bit            # the agent's log collector
grep BIOAF_LOG_DELIVERY docker/.env                              # unset means stdout
docker inspect -f '{{.HostConfig.LogConfig.Type}}' $(docker compose -f docker/docker-compose.yml ps -q backend)  # json-file
```

The agent writes to the VM's own project; the direct sink writes to the project in platform config.
They can differ: check the project before switching paths. A query such as
`jsonPayload.logger=~"^bioaf"` finds backend records sent through the agent.

### What is redacted, and what is not

- Every backend record (message, traceback, stack, structured fields) has the live values of these
  settings replaced with `***`: SMTP password, JWT signing key, the Slack client and signing secret
  slots, each `BIOAF_ENCRYPTION_KEYS` key, and the database URL password (decoded and as written in
  the URL). Values shorter than four characters and the repository's public placeholders are not
  matched. Only current values are matched: rotated-out secrets, other encodings, fragments, and
  organization credentials stored in the database (Slack bot tokens, webhook URLs, per-organization
  secrets) are protected only by not being logged in the first place.
- A record that cannot be formatted or sanitized is replaced by `log record withheld: ...` with its
  severity and logger, never passed through.
- Request logs carry method, path, status and duration only: no headers, query string or body.
- Output the backend does not own is shipped as written: Uvicorn's own stderr (including tracebacks
  for unhandled requests), other libraries' root-logger output, import-time errors, the database,
  frontend and nginx containers. nginx's default access log records full request lines, query
  strings included (for example password-reset `token` and Slack OAuth `code`/`state` parameters).
  Application redaction does not cover any of these.

### Delivery limits

Delivery is best effort. With the agent, records wait in Docker's log files and are shipped when the
agent recovers, subject to Docker's and the agent's own retention. With `direct`, records the
background transport cannot send are lost; on shutdown the direct sink gets 2 seconds to flush
after owned work has stopped.

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
