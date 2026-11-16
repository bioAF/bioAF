# Configuration

The backend settings are defined in [app/config.py](../backend/app/config.py). Host install/update settings
are consumed by `bioaf`, installer helpers and Compose; Organization and infrastructure configuration is
also stored in PostgreSQL. These are different settings sources.

## Where Settings Come From

Backend variables use the `BIOAF_` prefix. Process environment values take precedence over `.env` in the
backend's launch directory. Copying the root `.env.example` does not activate it unless it is placed in that
directory or its values are exported. Alembic and backend CLI commands load the same settings.

Production Compose reads `docker/.env`, using names such as `DATABASE_URL`, `SECRET_KEY` and `GCP_PROJECT_ID`
to populate the backend's corresponding `BIOAF_` variables. Configure values in the source the launcher
actually consumes. Adding a host variable that Compose does not forward will not configure the container.
For new operational overrides, review the service's `environment` mapping explicitly.

Do not publish `.env`, encryption keys, dumps or Terraform state. Host configuration contains secrets;
some database credentials are encrypted with `BIOAF_ENCRYPTION_KEYS`. The first key writes new values and
all listed keys can read existing ones. Preserve the required keys with recovery material.

## Execution and Logging

| Backend setting | Default or accepted values | Meaning |
| --- | --- | --- |
| `BIOAF_COMPUTE_MODE` | Required by the application: `local`, `kubernetes`/`k8s`, legacy `slurm` | Simulate mode-aware providers or use real execution; invalid/empty values fail startup |
| `BIOAF_ENVIRONMENT` | `development` enables internal interactive docs/schema | Production disables internal `/docs` and `/openapi.json` |
| `BIOAF_LOG_DELIVERY` | `stdout` or `direct`; default `stdout` | Choose one centralized log delivery path |
| `BIOAF_DEBUG` | Application default `false`; production Compose hardcodes `false` | Enables DEBUG-level application records and SQLAlchemy SQL echo when passed as `true`; changing `docker/.env` alone does not override production Compose |
| `BIOAF_ENCRYPTION_KEYS` | Required: one or more valid, comma-separated Fernet keys | Encrypt sensitive stored fields and read earlier ciphertext; model imports fail when the keyring is missing/invalid |

Production Compose supplies `kubernetes` when the host mode is unset/empty. Development Compose and
`.env.example` explicitly supply `local`. Local simulation does not cover every provider or external
integration. The database's `compute_stack` and install-level `cloud_provider` select backends independently
of compute mode. SLURM is not implemented despite acceptance of its legacy value.

`stdout` emits JSON for a host collector; `direct` sends through the GCP logging adapter where nothing else
collects container output. Do not configure both. See [Logging](deployment-guide.md#logging).

## Installation Metrics Settings

Reporting is opt-in and decided by an admin in the application; these host settings only restrict or
redirect it. See [Installation Metrics](installation-metrics.md).

| Backend setting | Default | Meaning |
| --- | --- | --- |
| `BIOAF_INSTALL_METRICS_DISABLED` | Unset (reporting follows the admin's choice) | `1` turns reporting off whatever was chosen and cancels pending reports; CI and development Compose set it |
| `BIOAF_POSTHOG_PROJECT_TOKEN` | Unset (the release image's public token) | Replaces the destination token; must be set together with `BIOAF_POSTHOG_HOST` |
| `BIOAF_POSTHOG_HOST` | Unset (`https://us.i.posthog.com`) | Replaces the ingestion host; an `https://` origin, set together with the token |

A partial or invalid pair disables delivery rather than mixing destinations. Production Compose forwards all
three from `docker/.env`.

## Webhook Destination Settings

Webhook subscriptions reach public HTTPS receivers by default; no allowlist entry is needed for them.
An internal LIMS on a private network needs an operator exception. Only whoever controls the backend's
environment can set it: admins manage subscriptions in the application but cannot grant exceptions there.

| Backend setting | Default | Meaning |
| --- | --- | --- |
| `BIOAF_WEBHOOK_INTERNAL_DESTINATIONS` | Unset (`[]`: public receivers only) | JSON list of internal receiver exceptions; read at startup, so a change needs a backend restart |

Example:

```json
[{"organization_id": 1, "origin": "https://lims.lab.example:8443", "cidrs": ["10.24.8.19/32"]}]
```

- `organization_id` must match the subscription's organization and `origin` must match its URL's scheme,
  host, and port exactly (no path, query, or wildcard).
- `cidrs` lists each receiver address exactly: a `/32` inside RFC1918 IPv4 or a `/128` inside IPv6 ULA.
  Subnets, public addresses, metadata, loopback, link-local, shared, and reserved addresses are rejected.
- Exceptions never allow plain HTTP or unverified TLS: give the internal receiver a certificate the
  backend trusts.
- An invalid value stops the backend at startup; the error names the setting, not its value.

Production Compose forwards the setting from `docker/.env`. Address classification cannot see
installation-specific NAT64 or routing translation; deployments that use such translation also need
network egress rules that block metadata and unauthorized internal destinations. See
[Webhooks](api/webhooks.md#allowed-destinations).

## File Verification Settings

bioAF checks an expected MD5 (from an upload or a fetchngs samplesheet) against the stored bytes in a
background worker (see [Files](api/files.md#checksum-verification)). Defaults suit most installs.

| Backend setting | Default | Meaning |
| --- | --- | --- |
| `BIOAF_FILE_VERIFICATION_LEASE_SECONDS` | `300` | How long a claimed verification may go without progress before another worker may take it over |
| `BIOAF_FILE_VERIFICATION_CHUNK_BYTES` | `8388608` (8 MiB) | Size of each ordered read while hashing |
| `BIOAF_FILE_VERIFICATION_JOB_DEADLINE_SECONDS` | `21600` (6 h) | A single attempt longer than this is abandoned and retried |
| `BIOAF_NFS_PRODUCER_UIDS` | Unset | Comma-separated UIDs that pipelines, notebooks and uploads write NFS as. Required for checksum verification on NFS; must not include the backend's UID |
| `BIOAF_NFS_MANAGED_COPY_RESERVE_BYTES` | `1073741824` (1 GiB) | Free space kept on NFS beyond the extra protected copy |

Production Compose forwards `BIOAF_NFS_PRODUCER_UIDS` from `docker/.env`. One verification runs at a
time per backend process. Files without an expected checksum need no read
at all; their size is checked from storage metadata.

## Export and Restore Settings

These are application defaults; production Compose forwards only the settings listed in its service
mapping. Limits are provisional operating bounds, not throughput guarantees.

| Backend setting | Default | Meaning |
| --- | --- | --- |
| `BIOAF_EXPORT_SCRATCH_DIR` | `/tmp/bioaf-exports` | Private export scratch |
| `BIOAF_EXPORT_TRANSFER_STALL_SECONDS` | 60 | Maximum idle interval for direct downloads |
| `BIOAF_EXPORT_SIGNED_MIN_TTL_SECONDS` | 3600 | Refuse a download link with less remaining lifetime |
| `BIOAF_BACKUP_SCRATCH_DIR` | `/tmp/bioaf-backups` | Private backup/restore workspaces |
| `BIOAF_RESTORE_MAX_DUMP_BYTES` | 10737418240 (10 GiB) | Actual downloaded dump limit |
| `BIOAF_RESTORE_SCRATCH_FLOOR_MIN_BYTES` | 3221225472 (3 GiB) | Minimum free scratch floor |
| `BIOAF_RESTORE_SCRATCH_FLOOR_PERCENT` | 10 | Percentage floor; the larger floor applies |
| `BIOAF_RESTORE_DOWNLOAD_TIMEOUT_SECONDS` | 900 | Download deadline |
| `BIOAF_RESTORE_TOC_TIMEOUT_SECONDS` | 60 | Archive table-of-contents check deadline |
| `BIOAF_RESTORE_PG_RESTORE_TIMEOUT_SECONDS` | 1800 | Database import deadline |
| `BIOAF_RESTORE_PREPARATION_TIMEOUT_SECONDS` | 2700 | Total preparation deadline |
| `BIOAF_RESTORE_DB_VOLUME_DIR` | Empty outside production Compose | Read-only path used to measure database-disk headroom |

Production Compose sets the database volume path to `/var/lib/bioaf/db-volume`. An unset path reports
headroom as not measured; a configured but unreadable path refuses preparation. The export's 2 GiB source
limit, 10,000-file limit, provenance bounds and single slot are defined in code, not configurable by these
variables. See [Data Exports](guides/data-exports.md) and [Backup & Recovery](user-guide-admin.md#backup--recovery).

Host-side image/cache/dump retention knobs are documented with their consumers under
[Disk use and deployment generations](deployment-guide.md#disk-use-and-deployment-generations).
