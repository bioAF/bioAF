# ADR-065: BAL Normalized Contract, Capabilities, and the Category/Backend Rule

**Status:** Accepted
**Date:** 2026-06-08
**Deciders:** Brent (repository owner)
**Supersedes:** [ADR-020](ADR-020-bioaf-adapter-layer.md) (BioAF Adapter Layer)

---

## Context

[ADR-020](ADR-020-bioaf-adapter-layer.md) introduced the BioAF Adapter Layer (BAL) to decouple application logic and UI from specific infrastructure providers. A 2026-06 architecture review (`local/arch_review/02-bal-modularity.md`) found the BAL had *largely failed that goal* while succeeding as a narrow seam:

- The promised normalized data models were never built; every BAL method returned an untyped `dict` whose shape was implicitly Kubernetes-specific.
- Roughly 36 service/api files imported a GCP or Kubernetes SDK directly, bypassing the BAL entirely (storage, billing, secrets, messaging, image builds, GCP config, orphaned-resource cleanup).
- Provider-specific columns (`k8s_job_name`, `k8s_namespace`, `gcs_uri`) flowed from adapter to database to UI.
- Every non-stub adapter imported back up into the service layer (a layering inversion).
- The registry hardcoded `GCEWorkNodeProvider` and always built `KubernetesCellxgeneProvider`, and callers reached past the base interface into concrete-only methods and private clients.

The "BAL rework" (phases 0-9, tracked in `local/bal-rework/`) corrected these. This ADR records the resulting contract as the durable artifact, supersedes ADR-020, and revisits the GCP-only stance of [ADR-001](ADR-001-gcp-only.md).

ADR-020 also conflated two abstractions: "swap the compute scheduler" (Kubernetes vs SLURM) and "swap the cloud" (GCP vs AWS/on-prem). This ADR separates them explicitly via the category/backend rule.

---

## Decision

### 1. The category/backend rule (the governing principle)

There are two axes, and they must not be conflated:

- **Category (the interface, the "what"):** compute, storage, notebook, work-node, cellxgene, plus the platform-service categories secrets, messaging, observability, IAM, billing. Application code depends on these.
- **Backend (the implementation, the "how"):** Kubernetes, GCS, GCE, SLURM, NFS, EKS, S3, BigQuery, Pub/Sub, Secret Manager, Cloud Logging, etc.

> **Rule:** No module outside `backend/app/adapters/` may import a cloud or Kubernetes SDK (`google.cloud.*`, `kubernetes`, `boto3`/`botocore`, ...) or otherwise name a backend. Application code obtains a provider for a category and receives **typed normalized models**. `install-gcp.sh` (outside the application) is the one sanctioned exception.

This rule is mechanically enforced (see section 5), not merely documented.

### 2. The normalized-model contract

Every BAL method that returns structured data returns a typed Pydantic model defined in [`backend/app/adapters/models.py`](../backend/app/adapters/models.py), never a bare `dict`. The models include `JobSubmitResult`, `JobStatus`, `JobProgress`, `ClusterStatus`, `ClusterMetrics`, `CostEstimate`, `StorageMetrics`, `StoredObject`, `ObjectMetadata`, `SessionStatus`, `VmStatus`, `TerminationResult`, and `CellxgeneInstance`.

Two invariants every model holds:

1. **Normalized fields are first-class** and mean the same thing on every backend. Core logic and the UI may depend only on these.
2. **Backend specifics go in an opaque `provider_details` dict.** Extras meaningful to one backend only (K8s pod name, GKE phase, GCS md5) live there; detail/disclosure views may render it, but core logic must never branch on it.

The data model is provider-neutral to match: `compute_job_ref` + `provider_metadata` replace `k8s_job_name`/`k8s_namespace`/`k8s_pod_name`, and `storage_uri` (synonym-backed) replaces `gcs_uri`. The UI renders from the normalized fields and exposes raw provider metadata behind a "Provider details" disclosure.

### 3. The capability contract

A backend may not support every operation in a category. Each provider declares a [`ProviderCapabilities`](../backend/app/adapters/capabilities.py) model with boolean flags:

`cost_estimation`, `autoscaling`, `ssh_exec`, `spot_retry`, `job_report`, `signed_url_upload`, `storage_tier_metrics`, `notebooks`, `cellxgene`, `work_nodes`, `messaging`, `billing`.

- Capabilities are exposed at bootstrap (`/api/bootstrap/status`) and consumed by the frontend `useCapabilities()` hook to gate optional UI (cost columns, autoscale/spot controls, SSH/connect, signed-URL upload, cellxgene/work-node nav).
- The server enforces the same contract: `require_capability(flag)` raises `CapabilityNotSupported`, mapped by a single FastAPI handler to HTTP 422, registered on both the main app and the mounted v1 integrations sub-app.
- **Capability** (does this backend support the operation) is distinct from **component availability** (is this optional component currently enabled/healthy, gated by `ComponentState`). The two are independent and both consulted.

### 4. Provider categories and the backend matrix

Two kinds of provider live under `adapters/`:

**Runtime providers** (resolved per-install from `platform_config` by the adapter registry):

| Category | Interface | GCP/K8s backend (shipped) | SLURM/NFS backend | AWS / on-prem |
|---|---|---|---|---|
| Compute | `ComputeProvider` | Kubernetes (GKE Jobs) | stub (planned) | EKS / kubeadm (slot) |
| Storage | `StorageProvider` | GCS | NFS (built, Phase 7) | S3 / filesystem (slot) |
| Notebook | `NotebookProvider` | Kubernetes (Jupyter/RStudio pods) | stub (planned) | slot |
| Work-node | `WorkNodeProvider` | GCE VMs (ADR-043) | (n/a) | EC2 / bare VM (slot) |
| Cellxgene | `CellxgeneProvider` | Kubernetes | (n/a) | slot |

**Platform-service providers** (selected by a config-keyed factory at bootstrap, not the DB registry, because they are needed before the database/registry are up):

| Category | Interface | GCP backend | AWS / on-prem (slot) |
|---|---|---|---|
| Secrets | `SecretsProvider` | Secret Manager | Secrets Manager / Vault |
| Messaging | `MessagingProvider` | Pub/Sub (auto-ingest, ADR-024) | SNS-SQS / poll |
| Observability | `LogSinkProvider` | Cloud Logging | CloudWatch / stdout |
| IAM | `IamProvider` | `iam_admin_v1` | IAM / local accounts |
| Billing | `BillingProvider` | BigQuery billing export (ADR-028) | CUR / none |

Work-node and cellxgene are independent of the `compute_stack` and are selected by their own `work_node_backend` / `cellxgene_backend` config keys (default GCE / Kubernetes). They are documented GCP-shaped escape hatches, not yet provider-neutral in their signatures.

### 5. Enforcement

The rule in section 1 is enforced by a committed static-AST guardrail test ([`backend/tests/test_bal_layering.py`](../backend/tests/test_bal_layering.py)) that fails the build if:

- any module outside `adapters/` imports a forbidden SDK (a shrinking allowlist pins the remaining known exceptions), or
- any module inside `adapters/` imports from `app.services` (the layering inversion, now pinned at zero).

The allowlist only ever shrinks. The adapter -> service inversion is fully closed; adapters depend only on the leaf-ward `app/platform/` layer (`PlatformConfigService` + `credential_injector`, extracted in Phase 1).

---

## Consequences

**Positive:**

- The contract is written down and machine-checked, so a second backend is built to satisfy an interface rather than reverse-engineered from dict keys.
- ~83% of the original direct-SDK sprawl (36 files) is drained behind adapters; the rest is a small, enumerated, regression-guarded set.
- The UI is provider-agnostic for the normalized path; provider specifics are quarantined behind a disclosure and capability gates.
- The layering inversion is gone, restoring the testability/substitutability the pattern exists for.

**Negative / honest limits:**

- The abstraction is still only *proven* for storage (a real NFS adapter exists); SLURM compute/notebook adapters remain stubs, so compute swappability is contract-complete but not yet exercised end-to-end.
- A residual GCP-direct set remains by deliberate scoping, tracked in the guardrail allowlist: GKE/project provisioning (`container_v1`, `resourcemanager_v3`, `service_usage_v1`) and Tier-2 storage bucket-lifecycle operations. These drain in a later pass; the allowlist count is the burn-down metric (target: zero, save `install-gcp.sh`).
- Cloud Build image builds use the GCP REST API (not an SDK import), so they pass the guardrail but remain backend-coupled; an `ImageBuildProvider` seam is a future, optional refactor.

**Neutral:**

- No change to *what* each provider does functionally; only where the boundary sits and what type crosses it.

### Effect on ADR-001 (GCP-only)

[ADR-001](ADR-001-gcp-only.md) commits to GCP as the sole supported cloud. This rework does **not** ship a second cloud, but it deliberately creates the seams (the AWS/on-prem slots above) so that decision is reversible without re-litigating the application layer. ADR-001 should be revisited to state intent explicitly: GCP is the only *implemented* backend today; the architecture no longer *assumes* GCP. SLURM/NFS vs AWS/on-prem sequencing remains an open owner decision.

---

## References

- [ADR-020](ADR-020-bioaf-adapter-layer.md) (superseded) - the original BAL
- [ADR-001](ADR-001-gcp-only.md) (to revisit) - GCP-only
- [ADR-021](ADR-021-kubernetes-compute-backend.md), [ADR-022](ADR-022-gcs-storage-backend.md) - the shipped backends
- [ADR-024](ADR-024-gcs-auto-ingest.md) (messaging), [ADR-028](ADR-028-bigquery-billing-export.md) (billing), [ADR-043](ADR-043-work-nodes-gce-migration.md) (work nodes)
- `local/arch_review/02-bal-modularity.md` - the review that motivated this
- `backend/app/adapters/models.py`, `backend/app/adapters/capabilities.py`, `backend/tests/test_bal_layering.py` - the contract and its enforcement
