# ADR-008: Google Secret Manager for All Secrets

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF manages several categories of secrets: database credentials, API tokens (GitHub, SMTP, Slack), JWT signing keys, and potentially CellRanger license information. These secrets need to be stored securely, accessed by the control plane at runtime, and never leaked into logs, Terraform state, environment variables, or the GitOps repo.

Options considered:

1. **HashiCorp Vault:** Feature-rich but heavy operational overhead for a self-hosted platform. Running Vault requires its own HA deployment, unsealing procedures, and monitoring.
2. **Kubernetes Secrets:** Simple but stored unencrypted in etcd by default. Requires additional configuration for encryption at rest. Not accessible outside the cluster.
3. **Google Secret Manager:** Managed service, IAM-integrated, audit-logged, versioned. Native to GCP.
4. **Environment variables:** Insecure. Visible in process listings, container inspect, and crash dumps.

## Decision

All secrets are stored in Google Secret Manager. The bioAF control plane accesses secrets via the Secret Manager API using a dedicated GCP service account with minimal permissions (secretmanager.secretAccessor role on specific secrets only).

### Secrets Inventory

| Secret | Created By | Accessed By |
|---|---|---|
| Cloud SQL credentials | Terraform (auto-generated) | bioAF API |
| JWT signing key | bioAF (auto-generated on first boot) | bioAF API |
| GitHub PAT (GitOps) | Admin (during setup or later) | bioAF API |
| SMTP credentials | Admin (during bootstrap) | bioAF notification service |
| Slack webhook URLs | Admin (in settings) | bioAF notification service |
| CellRanger license path | Admin (during SLURM setup, optional) | SLURM compute nodes |
| Service account keys (GKE ↔ SLURM) | Terraform (auto-generated) | SLURM controller, compute nodes |

### Access Pattern

- bioAF control plane runs as a GKE workload with Workload Identity mapped to a dedicated service account
- The service account has `roles/secretmanager.secretAccessor` on the specific secrets it needs (not project-wide)
- Secrets are fetched at application startup and cached in memory (not written to disk)
- Secret rotation is supported by creating new secret versions; the application reads the latest version

## Rationale

- **Managed service eliminates operational burden.** No Vault cluster to run, monitor, unseal, or back up.
- **IAM integration is native.** Workload Identity on GKE provides clean, auditable access without static service account keys.
- **Audit logging is built in.** Cloud Audit Logs record every secret access, which feeds into the overall compliance story.
- **Versioning supports rotation.** Secrets can be rotated by creating a new version without downtime.
- **Cost is negligible.** Secret Manager pricing is based on access operations and active secrets: for bioAF's usage (dozens of secrets, accessed at startup), the cost is effectively zero.

## Consequences

- Terraform must create Secret Manager secrets during foundation provisioning and populate them with auto-generated values.
- The bioAF FastAPI application must include a Secret Manager client that fetches secrets on startup.
- Secrets must never appear in: Terraform state files (use `sensitive = true` on outputs), application logs (redact in logging middleware), the GitOps repo (the `.tfvars` file references secret names, not values), or API responses.
- If the Secret Manager API is unreachable (network issue, permission change), the bioAF control plane will fail to start. Health checks must surface this clearly.
- For SLURM compute nodes (which run on Compute Engine, not GKE), secrets are accessed via instance metadata or a startup script that reads from Secret Manager using the node's service account.

## Addendum (2026-05-12): Distinction from data-at-rest DEK

ADR-047 introduces `BIOAF_ENCRYPTION_KEYS`: a Fernet keyring used to encrypt sensitive *DB columns* at rest. That secret is operationally distinct from the ones covered here:

- **Deployment secrets (this ADR):** database password, JWT signing key, SMTP credentials, etc. Stored in Secret Manager, fetched once at backend startup, scoped to "things the platform needs to authenticate to other systems."
- **Data-encryption key (ADR-047):** the key that protects ciphertext sitting inside `pg_dump` output. Sits in the env var because every read/write needs it on the hot path, and because rotation needs to atomically expose both old and new keys to the same process.

The DEK could be stored *in* Secret Manager as a deployment secret (fetched at startup and injected into the env), and that may be a future enhancement. A further extension is KMS-wrapping the DEK so the env var holds a wrapped value that GCP KMS unwraps on demand; ADR-047 leaves this door open with the deferred `BIOAF_KEK_KMS_RESOURCE` flag.
