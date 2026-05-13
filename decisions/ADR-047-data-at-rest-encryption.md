# ADR-047: Data-at-Rest Encryption via App-Level Fernet

**Status:** Accepted
**Date:** 2026-05-12
**Deciders:** Brent (repository owner)

---

## Context

A code-level audit of bioAF found no data-at-rest encryption. Sensitive columns sat in PostgreSQL as plaintext: per-org Slack/SMTP secrets, SSH private keys, work-node heartbeat tokens, Slack bot tokens and webhook URLs, and the GCP service account key stored in `platform_config`. `pg_dump` runs in custom format without encryption and uploads the file to a GCS bucket (`bioaf-backups-{project_id}`). Anyone with read access to that bucket had plaintext copies of every secret.

This is a real exposure: backup bucket access is a smaller blast radius than primary DB access, so it is more permissively shared in practice (e.g., off-cluster restore tooling, ad-hoc inspections). Plaintext in `pg_dump` collapses that distinction.

---

## Decision

Encrypt sensitive columns at the application layer using Fernet via a SQLAlchemy `TypeDecorator`. Because ciphertext lives in the row itself, it travels into `pg_dump` output automatically and backup-bucket exposure no longer leaks secrets.

### Key components

- **Encryption service:** `app/services/encryption_service.py` wraps `cryptography.fernet.MultiFernet`. Exposes `encrypt(plaintext)`, `decrypt(ciphertext)`, and `looks_like_ciphertext(value)`. None passes through so nullable columns just work.
- **TypeDecorator:** `app/types.EncryptedString` stores ciphertext as `TEXT`. Model code never sees the encryption boundary; it reads and writes plaintext.
- **Sensitive columns** (encrypted via `EncryptedString`):
  - `organizations.{smtp_password, slack_client_secret, slack_signing_secret}`
  - `session_credentials.ssh_private_key`
  - `compute_sessions.heartbeat_token`
  - `slack_installations.bot_token`
  - `slack_webhooks.webhook_url`
- **platform_config special case:** the table is key/value, so blanket encryption would over-encrypt non-secrets. `PlatformConfigService` is the single read/write point and consults `SENSITIVE_PLATFORM_CONFIG_KEYS` (currently `{"gcp_service_account_key"}`) to decide which rows to encrypt.
- **Key source:** `BIOAF_ENCRYPTION_KEYS` env var, comma-separated. First key is the primary writer; all keys are accepted readers. `install.sh` generates a Fernet key into `docker/.env` on first run and on `--force`. Helm reads the value from a Kubernetes Secret named in `values.yaml`.
- **Migration 076** backfills ciphertext for every sensitive column on existing rows. Idempotent (skips rows where the value already starts with `gAAAA`) and aborts cleanly if `BIOAF_ENCRYPTION_KEYS` is unset.
- **Startup check:** `app/config.validate_encryption_keys()` runs in the lifespan handler beside `validate_jwt_secret()`. Missing or malformed keys fail the container immediately.

### Why app-level Fernet vs alternatives

- **pgcrypto:** would put the key inside Postgres (in queries or roles), which is the same blast radius as the data. Defeats the point.
- **Filesystem-level encryption (LUKS, GCE PD-CMEK):** protects the disk, not `pg_dump`. The threat model here is the backup, not the volume.
- **Per-column AES with custom KMS:** rotation tooling does not exist; Fernet bundles version + IV + MAC and `MultiFernet` makes rotation a single env-var change.
- **GCP KMS-wrapped DEK:** considered and deferred to a future v2. A `BIOAF_KEK_KMS_RESOURCE` flag could be added later that treats `BIOAF_ENCRYPTION_KEYS` as a wrapped DEK; the current shape leaves that door open.

### Rotation design

`MultiFernet` accepts an ordered list of keys; the first is the writer, the rest are accepted readers. To rotate:

1. Generate a new key.
2. Prepend it to `BIOAF_ENCRYPTION_KEYS`.
3. Restart the backend. New writes use the new key; old rows still read via the legacy key.
4. Re-encrypt rows in place (CLI runs the same backfill loop as migration 076).
5. Remove the old key from `BIOAF_ENCRYPTION_KEYS` and restart.

See `documentation/runbook-key-rotation.md`.

### Key-loss implications

Losing `BIOAF_ENCRYPTION_KEYS` makes every encrypted column unrecoverable, even with a full backup. The key must be stored alongside the backup pointer (GCS bucket + project ID) in any disaster-recovery plan. See `documentation/recovery-and-encryption.md`.

---

## Out of scope

- **ADR-030 Pod-spec exposure** (bcrypt hash visible via `kubectl describe`): separate side channel, addressed in a follow-up.
- **CMEK on the backups GCS bucket:** independent defense-in-depth.
- **Recovery kit feature** (paused): when it resumes, `BIOAF_ENCRYPTION_KEYS` is part of what the kit must include.
- **KMS-wrapped DEK:** future v2 if the threat model expands to in-memory exposure of the DEK at rest in the env var.

---

## Consequences

**Positive**

- `pg_dump` output is no longer a path to plaintext secrets.
- Application code stays plaintext-only; the encryption boundary lives in one TypeDecorator and one service.
- `MultiFernet` makes key rotation a documented, low-risk operation.

**Negative**

- An additional secret (`BIOAF_ENCRYPTION_KEYS`) operators must back up; losing it is unrecoverable.
- ORM-only access to encrypted columns. Direct SQL inspections of those columns now return ciphertext. Tests and ops tooling that grep for plaintext secrets must route through the ORM or `encryption_service.decrypt`.
- Ciphertext is meaningfully larger than plaintext; affected `String(N)` columns widened to `TEXT` in migration 076.

---

## References

- Spec: `documentation/spec-encryption-at-rest.md` (local-only)
- Migration: `backend/alembic/versions/076_encrypt_sensitive_columns.py`
- Service: `backend/app/services/encryption_service.py`
- Type: `backend/app/types.py`
- Runbook: `documentation/runbook-key-rotation.md`
- Recovery: `documentation/recovery-and-encryption.md`
