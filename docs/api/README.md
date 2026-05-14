# bioAF Integration API

Public, key-authenticated REST surface for LIMS systems (and any external
caller) to push and pull project, experiment, sample, and file metadata.

- **Base URL**: `https://<your-host>/api/v1/integrations`
- **OpenAPI**: `GET /api/v1/integrations/openapi.json` (authoritative schema)
- **Swagger UI**: `https://<your-host>/api/v1/integrations/docs`

The OpenAPI document is the source of truth for request and response
shapes. The markdown files in this directory exist as a human-readable
overview and quickstart, plus a stable reference for the contract decisions
behind each endpoint.

## Contents

- [Authentication and Authorization](auth.md): how to mint a key, what scopes
  mean, how the SA role and key scopes combine.
- [Conventions](conventions.md): error envelope, idempotency, pagination,
  external IDs, status codes.
- [Projects](projects.md): create, read, update, list, lookup by external_id.
- [Experiments](experiments.md): create, read, update, list, lookup by
  external_id. Status writes are not permitted.
- [Samples](samples.md): create, read, update, list, lookup by external_id.
  QC and status writes are not permitted.
- [Files](files.md): read-only metadata. Bytes flow only through the
  internal upload pipeline.
- [Webhooks](webhooks.md): event catalog, delivery format, HMAC signature,
  retry and dead-letter behavior.

## What this API is not

- Not a file-upload surface. Use the internal ingest pipeline; external
  systems learn about new files via webhooks and can read metadata only.
- Not a status-machine controller. Experiment and sample status moves are
  driven by bioAF internal services (pipeline completion, QC review, etc.).
  Public POST and PATCH reject `status`/`qc_status` writes.
- Not a UI for managing service accounts or keys. That lives in
  Settings > Users & Accounts. The endpoints under `/api/admin/...` are
  JWT-authenticated and not part of this public surface.
