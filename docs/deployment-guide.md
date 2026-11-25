# bioAF Deployment Guide

## Prerequisites

- **Docker** and **Docker Compose** (v2 plugin)
- **Git**
- **openssl** (for secret generation during install)
- **Python 3.10 or newer** on the host, with its standard library (host management helpers)

The application services run in Docker. Host management also uses the host's `python3` (the backend
container's interpreter does not count): `./install.sh`, `./bioaf setup`, `start`, `restart`, `build`,
`update` and the update agent check it before changing anything and stop with installation guidance if it
is missing, older than 3.10 or incomplete. `help`, `status`, `stop`, `logs` and commands that only run in
the backend container work without it. If the distribution's own `python3` is older than 3.10 (for example
Ubuntu 20.04, Debian 11, RHEL 9 or Amazon Linux 2023), installing its `python3` package does not help:
install a newer interpreter beside it (for example `python3.11`) and link it as `/usr/local/bin/python3`,
which comes before `/usr/bin` on the default PATH of both login shells and systemd services, so `./bioaf`
and the update agent both use it. The error message prints these steps. Managed Linux installs use
systemd for the update agent and, on GCE, the Ops Agent. Cloud provisioning scripts run on your local
machine and can install the cloud CLI. See [Configuration](configuration.md) for settings sources and
[Current Architecture](architecture.md) for workload boundaries.

## One-Command GCP Setup

The fastest path from zero to a running instance on Google Cloud. Run this
on your local machine (macOS or Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/bioAF/bioAF/main/install-gcp.sh | bash
```

The script installs/checks local cloud tooling, authenticates, selects the project and enables the
services declared in `install-gcp.sh` (not just Compute, Storage and IAM). It creates scoped service
accounts and impersonation bindings without downloading JSON keys, configures networking, provisions
the VM and writes a local prefill file at `~/.bioaf/prefill.yaml`.

Use its automatic handoff, or follow the printed manual worksheet to copy the prefill to
`~/.bioaf-prefill.yaml` on the VM and connect there. On that VM, after the prefill has been copied:

```bash
git clone https://github.com/bioAF/bioAF.git
cd bioAF
./bioaf setup --prefill "$HOME/.bioaf-prefill.yaml"
```

Use the VM name, zone, project and account details printed by your installer rather than assuming the
defaults. The prefill supplies the VM-default credential and bootstrap/Sheets-reader account settings
for the web wizard. Open the printed URL and use the setup code to complete administrator setup.

### Pinned installer versions

The one-liners above run the installer from `main`, and an installer run that
way downloads its helper files (`installer/*.sh`) from `main` too: a piped
script cannot tell which URL it came from. To install from a release tag or a
commit, pass the same ref to the script with `--ref` (or `BIOAF_INSTALLER_REF`):

```bash
REF="vYYYY.M.N"  # Replace with an actual release tag or commit
curl -fsSL "https://raw.githubusercontent.com/bioAF/bioAF/$REF/install-gcp.sh" \
  | bash -s -- --ref "$REF"
```

Release tags use `vYYYY.M.N` with an unpadded month, for example `v2026.9.1`.
Pinning needs an installer that has `--ref`: releases after v2026.11.22. Earlier
installers (v2026.11.22 and before) do not know the option, and always download
their helpers from `main`, so pinning them is not possible.

`main` moves; use a commit when you need the exact same files again. A ref that
does not exist, or helpers that fail to download completely, stop the
installer; it never falls back to `main`. A script run from a clone uses the
helpers beside it and never looks in the current directory.

`--ref` governs only the installer's helper files. The automatic setup it
offers at the end still clones bioAF on the VM and pulls `main` before running
`./bioaf setup`; to set up another version there, choose the manual option and
run `./bioaf setup --version YYYY.M.N` with the intended numeric release on the VM.

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

Pass options after `bash -s --` in the one-liner, or to the cloned script, e.g.
`./install-aws.sh --region us-east-1 --ssh-cidr 203.0.113.7/32` (the
`--option=value` form works too; `--help` lists them). SSH (port 22) is opened
only to your current public IPv4 address unless you pass `--ssh-cidr`; if that
address cannot be detected the installer stops before creating anything and
asks for `--ssh-cidr`. Reusing an existing `bioaf-allow-web` security group
keeps any SSH rules it already has, and the installer lists broader ones.
Open the printed URL, enter the setup code and follow the wizard. AWS has S3, EKS, EC2 and image-build
implementations, but several platform-service factories and cellxgene parity remain incomplete. A
successful EC2 host install does not prove each workload path works. Review
[Cloud support](architecture.md#cloud-support) before relying on a specific AWS workflow.

## Quick Deploy (Existing Server)

If you already have a Linux server, install Docker, the Compose v2 plugin, Git, openssl and Python 3
(3.10 or newer) before using the host management script. Verify Python 3 explicitly:

```bash
python3 --version
git clone https://github.com/bioAF/bioAF.git
cd bioAF
./bioaf setup
```

`setup` handles everything automatically:

1. Checks prerequisites (Docker, Docker Compose, Git, openssl, Python 3.10+)
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

All four services run as Docker containers orchestrated by Docker Compose. nginx handles TLS termination
and routing, including `/api/v1/integrations` to the same FastAPI backend. HTTP redirects to HTTPS except
for health checks and the ACME challenge path. The backend owns its workers and Terraform subprocesses;
Kubernetes and Work Node VMs are separate cloud resources. See the [architecture diagram](architecture.md).

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
./bioaf migrate-down REVISION  # Replace REVISION; also backs up first
./bioaf backup              # Dump database to backups/ directory (gzipped, verified)
./bioaf seed path/to/script.py  # Run a seed script inside the backend container
./bioaf dbshell             # Open a psql session
./bioaf reset-db            # Drop and recreate database (destructive, with confirmation)
```

### Check for shared file storage references

Before this release, two files uploaded into one experiment with the same name were stored at the same
path, so the second upload replaced the first's bytes while both records stayed. New uploads are stored
under each file's own ID. To find records that already share one stored object, run this read-only query
in `./bioaf dbshell`. It changes nothing, and bioAF never runs it on its own.

```sql
WITH refs AS (
    SELECT id, organization_id, deleted_at, storage_deleted,
           COALESCE(NULLIF(storage_uri, ''), NULLIF(gcs_uri, '')) AS uri
    FROM files
)
SELECT uri,
       COUNT(*) AS total_references,
       COUNT(*) FILTER (
           WHERE deleted_at IS NULL AND storage_deleted IS FALSE
       ) AS live_references,
       ARRAY_AGG(id ORDER BY id) AS file_ids,
       ARRAY_AGG(DISTINCT organization_id) AS organization_ids
FROM refs
WHERE uri IS NOT NULL
GROUP BY uri
HAVING COUNT(*) > 1
ORDER BY total_references DESC, uri;
```

Reading the result:

- Each row is one stored URI that more than one file record names. `live_references` counts records that
  are not deleted and whose bytes are not marked deleted; the other references are historical.
- A row shows only that records share a reference. It does not show which bytes each record originally
  held, or whether an older object version can recover them. Some shared references are intentional.
- The query matches exact URIs only. Different spellings of one object, or references to object versions,
  need separate inspection, as do uploads still waiting to be filed (`upload_checkpoints` rows in stage
  `received` or `cancelling`).
- The result spans every organization on the installation; share it only with installation operators.
- Investigating or repairing what it finds (restoring versions, rewriting URIs, deleting duplicates) is
  separate work. bioAF does not change these records or objects.

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
`INFO` and above. Passing `BIOAF_DEBUG=true` to the application enables `DEBUG`, but production Compose
hardcodes it to `false`; a value in `docker/.env` alone does not change that mapping. There is no text format and no switching on
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
they install and reconfigure no agent. They accept direct delivery only when the agent config
(`/etc/google-cloud-ops-agent/config.yaml`) is absent, contains only comments, or contains the line
`# bioaf-log-delivery: no-container-logs`, which you add after checking that your config reads no
container logs. A bioAF-managed or previously shipped config, any other config, and a config that
cannot be read all stop the command with an error. bioAF does not try to work out what an arbitrary
agent config collects.

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
./bioaf update YYYY.M.N     # Replace with the intended release; month is unpadded
```

This backs up the database, fetches the target version tag from GitHub,
pulls pre-built images, checks the new backend's configuration, restarts
services, upgrades the database, and waits until the backend reports ready.
The new release's image tag is written to `docker/.env` only after its
configuration check passes, so a rejected check leaves the current release
pinned and running.

From the UI, admins can click the **Install Update** button on the
**Settings > Platform Info** page when a new version is available. This
triggers the same update process through a host-side update agent.

Each update is recorded as an operation in `host-state/deployments.json` (a random ID, its original start
and source version, its target and completion time); a retry of the same unfinished target keeps it. When an
update completes, the updater asks the backend to record it for
[installation metrics](installation-metrics.md). A report is reserved only if an admin chose to share before
the update started, and a failure there never changes the update's outcome. To stop all reporting on a host,
set `BIOAF_INSTALL_METRICS_DISABLED=1` in `docker/.env` and restart the backend. A deliberate copy of an
installation that keeps running beside the original needs its own identity:
`./bioaf reset-install-metrics-identity --confirm`.

An update never changes cloud infrastructure. When the new release's infrastructure definitions differ from
what this installation last applied, the updater asks the backend to record a notice (the backend also
checks at startup), and administrators apply the changes from the app. See
[Infrastructure Changes After an Update](user-guide-admin.md#infrastructure-changes-after-an-update).

### Running work nodes and the update

Work nodes launched before this release keep the key the backend uses to save their outputs only in the
running backend's memory. Before the updater replaces the backend, it checks (from the new release's image,
against your cloud provider's list of work node VMs) that none of those older work nodes may still exist.
If any might, the update waits and says:

> Some older work nodes are still running. Finish saving your work and stop them when convenient, then
> retry the update. bioAF will leave them running.

Nothing is stopped for you, and the current installation keeps running. Use **View work nodes** and
**Check again** on **Settings > Platform Info**. If the check cannot reach your cloud provider, the update
says **bioAF could not check whether this update is safe. Your current installation is still running. Try
again shortly.** While the backend is replaced, new work node launches are paused for a few minutes and
then reopen; work nodes launched after this release keep running through later normal updates.

For the final check the updater freezes the old backend (`docker pause`, which keeps its memory and so
those keys), waits about 30 seconds so any VM it had already requested shows in the provider's list, and
checks again. If the check passes, the frozen backend is stopped and replaced, never resumed, so a launch
it accepted just before launches paused cannot start a VM afterwards. If the check fails, or anything
before the restart fails, the old backend resumes. The app does not answer during that window, so the
restart warning is shown before it. If the updater itself is killed while the backend is frozen,
`./bioaf start` resumes it.

**One-time retry from an older release.** An updater from v0.13.0 through v2026.11.21 cannot run that
check, so the first attempt only prepares the new update helper and stops without changing bioAF:

> The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue.

An older update page may first show **Update failed: encryption key bootstrap failed** or **Update failed:
Update process exited with code 1**. That is this preparation step, not a problem with your encryption key:
do not change any keys. Retry the update (**Install Update** again, or `./bioaf update YYYY.M.N`); the retry
runs the new updater and its work node check. Releases older than v0.13.0 need support before updating
while work nodes are running.

### The update agent

The agent is the `bioaf-update-agent` systemd service. It runs as the
account that owns the checkout and watches `update-requests/`, which the
backend container can write. It treats every file there as untrusted: a
trigger is read once without following links, must be a regular JSON file
with a `version` like `1.2.3` (optional integer `upgrade_id`, optional
string `requested_at`), and anything else is removed without running an
update. Only `./bioaf update <version>` is ever run.

- **Status.** `update-status/current.json` is written by `./bioaf update`
  and, when the update stopped without recording why, by the agent. Both
  write a complete JSON record through a private staging directory
  (`host-state/`, never mounted) and rename it into place at mode 0644; a
  link planted in `update-status/` is replaced, not followed.
- **Log.** The agent and the update it runs log to `host-only/agent.log`
  (directory 0700, file 0600, never mounted). If that log cannot be opened
  safely (wrong owner, permissions, or a link), they log to the service
  output instead: `sudo journalctl -u bioaf-update-agent`. The older
  `update-status/agent.log` is no longer written; you can delete it.
- **Python.** The agent checks the host's `python3` when it starts. Without a
  usable one (3.10 or newer) it exits before touching triggers, status or its
  log, and says why in `sudo journalctl -u bioaf-update-agent`; systemd keeps
  retrying it. Install `python3`, then `sudo systemctl restart bioaf-update-agent`.
- **Unit.** `./bioaf setup` (after any `--version` checkout), `./bioaf
  start` and every update reconcile `/etc/systemd/system/bioaf-update-agent.service`
  with the release's template, rendered with the checkout path and account.
  The installed unit starts with `# bioaf-managed: update-agent-unit/v1 sha256=...`.
  A current unit is left alone; a unit an earlier release wrote for this
  checkout is replaced after `systemd-analyze verify`, then reloaded,
  enabled and restarted, and restored if that fails. Any other unit is left
  unchanged and the step fails, with the candidate saved to
  `host-only/update-agent-unit-v1.candidate`; add the line
  `# bioaf-managed: operator` to keep managing it yourself. The checkout
  path may contain only letters, digits and `. _ / + @ -`.
- **Restart timing.** A stopped agent is started. A running agent whose
  code differs from the code on disk is restarted. Before changing anything
  bioAF pauses the agent (SIGSTOP) and continues only if it was between
  polls, so no update can start before the restart; every start or restart
  must leave the agent still running a few seconds later, or the previous
  unit is restored. If the agent is running an update (for example the
  update that installs this release), nothing changes then: a transient
  `bioaf-update-agent-activation` unit, running as the checkout's account,
  repeats the same steps once that update has finished (outcome:
  `sudo journalctl -u bioaf-update-agent-activation`). A failure is reported
  and does not stop `./bioaf start` or an update; `./bioaf setup` stops.
- **From an older release.** The first update from a release before this
  one runs that release's agent and updater, so that update's own status
  and log writes still use the older code. The new release's
  `install.sh ensure-encryption-key` step schedules the unit and agent
  update for when that update finishes, so later updates use the new agent.

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

About a minute after the backend starts on a newly upgraded database, it also
reports experiments whose GEO export needs a decision about legacy files: files
without a Sample link whose names match a Sample (GEO export no longer uses
names to decide which Sample a file belongs to). The report runs in the
background with a time limit; it never stops or fails an update and the
application is already serving while it runs. Counts appear under
`geo_association_advisory` in `update-status/schema-upgrade.json`; the file and
Sample details are in a private
`backups/bioaf_geo_association_advisory_<revision>_<time>.jsonl`. Resolve each
one in the running application from the experiment's **Export to GEO**,
**Check Readiness** (see the GEO export guide). Only those experiments' GEO
downloads wait for a decision. If the report could not complete (for example a
timeout), it says `failed` and runs again the next time the backend starts;
GEO readiness checks every export regardless.

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
   to it, and restore into a fresh database. Verify the dump against its manifest before stopping services.
   The example uses the default `bioaf` database/user names; adapt these if your installation changed them.
   Replace `TIMESTAMP` with the chosen dump's timestamp and check the file exists before proceeding:

   ```bash
   DUMP="backups/bioaf_pre_upgrade_TIMESTAMP.dump"
   if test -s "$DUMP"; then
       ./bioaf stop &&
       docker compose -f docker/docker-compose.yml --env-file docker/.env up -d db &&
       docker compose -f docker/docker-compose.yml --env-file docker/.env exec -T db \
           sh -c 'dropdb -U bioaf bioaf && createdb -U bioaf bioaf' &&
       docker compose -f docker/docker-compose.yml --env-file docker/.env exec -T db \
           pg_restore -U bioaf -d bioaf --exit-on-error --single-transaction --no-owner --no-acl < "$DUMP" &&
       ./bioaf start
   else
       printf '%s\n' "Dump missing or empty; no services or databases were changed."
   fi
   ```

   A gzipped SQL backup from `./bioaf backup` or an older update restores the
   same way with `gunzip -c backups/<file>.sql.gz | ... exec -T db psql -U bioaf -d bioaf`.
   Alembic downgrades are not a substitute: they do not undo data repairs.

### Disk use and deployment generations

`./bioaf update`, `./bioaf build` and `./bioaf setup` run one at a time on a host. A second one started while
another runs stops at once with "Another bioAF update, build or setup is running" (exit status 75) and changes
nothing; an in-app update refused that way is retried by the update agent when the first finishes.

Each records, in `host-state/deployments.json` (readable by the host operator only), which backend and frontend
images run now (**current**, by image ID), which ran before (**previous**), and an operation that has started
but whose images are not yet running (**candidate**). The current and previous generations' images also carry
their own local tag, shown as `local_tag` (`gen-<backend>-<frontend>`), so a rebuild under the same version
cannot remove the images a rollback needs; `images_present` is `false` if a generation's images are no longer
on the host (for example, removed by hand), and that generation cannot be rolled back to. If an update or build is interrupted, the next one says
"An earlier ... did not finish" and replaces it. `./bioaf build` changes the deployed version
(`BIOAF_IMAGE_TAG` in `docker/.env`) only after the build succeeds; the new images become current when
`./bioaf start` or `restart` runs them.

Before pulling or building, the updater frees space and checks it has enough:

| What | Rule | Override |
| --- | --- | --- |
| bioAF images | Removed unless a container (running or stopped) uses them, they are the current, previous or candidate generation, or they carry the version being installed. Other images are never touched. | - |
| Build cache | Never removed: it belongs to the host's shared Docker builder. Over 5 GiB, the updater says so and gives the command to free it | `BIOAF_BUILD_CACHE_BYTES` |
| Updater backups in `backups/` | The backups each update made (its recovery set) are kept for the current and previous generations. Of the rest, the 2 newest of each kind (`bioaf_pre_update_*.sql.gz`, `bioaf_pre_upgrade_*.dump` with its `.json`) are kept. Nothing is removed without a readable record. Backups from `./bioaf backup` (`bioaf_<timestamp>.sql.gz`) are never removed. | `BIOAF_KEEP_UPDATE_DUMPS` |
| Free space | 3.5 GiB for new images plus 1.5x the database for the backup must leave max(3 GiB, 10% of the disk) free, or the update stops before changing anything | `BIOAF_NEW_IMAGES_BYTES`, `BIOAF_DISK_FLOOR_MIN_BYTES`, `BIOAF_DISK_FLOOR_PERCENT` |
| Update agent log | `host-only/agent.log` rotates at 10 MiB, keeping `agent.log.1` and `agent.log.2` | `AGENT_LOG_MAX_BYTES` (agent environment) |

These budgets are provisional starting values. Removing an image's tag does not always free its bytes: layers
shared with kept images stay. During an update the host holds up to three generations of images (the incoming
one, current, and previous) until the next update removes the oldest.

Rolling back images is not the same as restoring the database: the previous generation's images may not run
against a database the new release migrated. To go back, follow the restore steps above with the backup listed
as the current generation's `recovery` in `host-state/deployments.json` (made before that update), set
`BIOAF_IMAGE_TAG` in `docker/.env` to the previous generation's `local_tag` shown there, and run
`./bioaf restart`. The two generations then swap: the one you returned to is current, and the one you left is
previous, so you can return to it the same way.

If the record is missing or damaged, the updater reports it and removes no images. A damaged record can be
deleted to start fresh: the next update or start records what is running, and images are pruned again from the
update after that.

## Reinstalling

This is a destructive reset for an intentional fresh installation. It removes the PostgreSQL volume,
host configuration (including encryption keys) and the default TLS certificate/key files. Before running
it, create and verify a database backup and save the matching `docker/.env`, encryption keyring, certificates
and any other recovery configuration outside the checkout. Retained cloud files and encrypted backup
records can still require the original keys after a new setup generates a different keyring.

After preserving that recovery material, the following resets an installation using the default paths:

```bash
./bioaf stop
docker compose -f docker/docker-compose.yml --env-file docker/.env down -v   # Destroys the database volume
rm docker/.env docker/certs/tls.crt docker/certs/tls.key
./bioaf setup
```

Removing the configuration and certificates makes `setup` generate fresh ones. This procedure does not
restore prior data or preserve the old encryption keys automatically. Ordinary image updates use the
[Updating](#updating) workflow and retain the database/configuration.

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

## Checksum Verification Storage Costs

When a file arrives with an expected MD5 (an upload that supplied one, or a fetchngs samplesheet that
lists one), bioAF compares it with the stored bytes before the file is ready:

- **GCS:** objects uploaded whole carry an MD5 that bioAF trusts. Composite objects do not, and are read
  in full.
- **S3:** only a single-part object stored with SSE-S3 has an ETag that is its MD5. Uploads and copies of
  8 MiB or more are multipart, and SSE-KMS/SSE-C objects never qualify, so plan for a **full read of
  every large checksummed file** on AWS (one GET of the object, streamed in 8 MiB chunks).
- **NFS:** a file a producer can still write cannot be proved, so bioAF first copies it into a
  read-only, backend-owned area and hashes the copy. The copy becomes the file's canonical location;
  the original stays where the pipeline wrote it.

### Provisioning NFS for verification

For each store that holds checksummed files (normally `raw` and `results`), before enabling uploads or
fetches with checksums on NFS:

1. Run the backend under its own UID. Pipelines, notebooks and work nodes must write NFS as different
   UIDs; list them in `BIOAF_NFS_PRODUCER_UIDS`.
2. Create `<store>/verified-files`, owned by the backend UID, mode `0755` (no group/other write).
3. The store directory itself must not let producers rename `verified-files` away: either producers
   cannot write it, or it has the sticky bit (`chmod +t`) with `verified-files` owned by the backend.
   Producers may mount `verified-files` read-only.
4. Size the export for one extra copy of every checksummed NFS file plus
   `BIOAF_NFS_MANAGED_COPY_RESERVE_BYTES`, and for one write plus two reads of each such file.

If these conditions are not met, checksum-required NFS files stay unready with the visible error
"Protected storage for verification is not configured" (`nfs_separation_unavailable`). This protects
against producers, not against an administrator or root deliberately changing storage.

## Post-Deploy Configuration

### SMTP

For email notifications and user invitations, configure SMTP in
Settings > Integrations after logging in.

### Component Activation

From **Infrastructure > Components**, enable the available notebook, pipeline and visualization components
your team needs. Prerequisites can include provisioned compute/storage and a ready image; a toggle does
not always start an independent Terraform apply. SLURM compute is a stub, not an activatable HPC stack.
See [Compute Stack Setup](guides/compute-stack-setup.md) for the supported paths.

### Backup Configuration

Review [Backup & Recovery](user-guide-admin.md#backup--recovery), including creation-time checksums,
legacy-backup confirmation, strict import and the one-hour Accept/Reject review. Preparation checks both
scratch space and, in production Compose, the read-only database-volume capacity view. The UI workflow is
separate from host/updater dumps.

Backups are configured with sensible defaults. Review and adjust retention
policies in Infrastructure > Backup & Recovery.

## Teardown

### Remove Managed Cloud Compute

Use **Infrastructure > Components > Teardown** for the managed compute stack. This action keeps storage
buckets. **Destroy Storage** is a separate destructive operation offered after compute removal. It empties
and deletes managed buckets and removes storage-module messaging resources, including GCP Pub/Sub topics
and subscriptions. On AWS it can also remove managed image-build infrastructure when deployed. Preserve
and verify files and required configuration first. See [Life After bioAF](life-after-bioaf.md).

Destroy Storage acts only on buckets bioAF has established as this installation's, pauses uploads and
stops before deleting anything while uploads are still running, and can be run again after a failure to
continue where it stopped. The next storage deployment creates buckets with new names. See
[Uploads and Destroy Storage](user-guide-admin.md#uploads-and-destroy-storage).

### Stop the Application Host

From the host checkout:

```bash
./bioaf stop
```

Stopping services keeps PostgreSQL's Docker volume and does not remove other cloud resources. Deleting
the application VM or running a volume-removal command such as `docker compose down -v` can destroy the
host database. That is not the same as cloud-compute teardown and is not required just to stop bioAF.
Save verified database backups, keys and host configuration before permanently removing the host or volumes.
