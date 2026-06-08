### Architecture

- Recontracted the BioAF Adapter Layer (BAL) so compute and storage backends are
  genuinely swappable ([ADR-065](decisions/ADR-065-bal-normalized-contract.md),
  superseding ADR-020). Every adapter method now returns a typed, backend-neutral
  normalized model, each backend declares a capability set that gates optional UI
  and is enforced server-side, and a committed import guardrail keeps cloud and
  Kubernetes SDKs out of application code. GKE, GCE, GCS, Secret Manager,
  Pub/Sub, Cloud Logging, IAM, and BigQuery billing now all route through BAL
  providers. Behavior on a GCP install is unchanged.
- Added a real NFS storage backend behind the storage provider, and a proxied
  file-upload path used automatically on backends without signed-URL upload.

### Pipeline runs and notebooks

- The Pipeline Run and Notebook session detail pages now show a generic
  "Provider details" disclosure for backend specifics (job/pod/namespace) instead
  of Kubernetes-only labels, so the views read the same regardless of compute
  backend.

### Fixes

- Optional controls (cost columns, cluster autoscale and spot toggles, SSH
  connect, cellxgene, work nodes, signed-URL upload) are now hidden when the
  active backend does not support them, rather than failing when used.
