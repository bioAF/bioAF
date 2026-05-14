# ADR-050: External IDs and Idempotent Writes

**Status:** Accepted
**Date:** 2026-05-13
**Deciders:** Brent (repository owner)

---

## Context

External LIMS systems own their own primary identifiers for projects, experiments, and samples. When a Benchling or LabKey instance pushes a project into bioAF, it cares that "MY-PROJ-2026-01" is the project, not whatever bioAF rowid we hand back. Network retries also mean the same logical create can hit our API twice; if the second call creates a duplicate row, we have corrupted the integration.

Today:

- `Sample.sample_id_external` already exists.
- `Project` and `Experiment` have no external_id column.
- No idempotency-key infrastructure exists; retries can produce duplicates.

The shape of the contract every future integration depends on is being locked in here, so it gets an ADR rather than being a spec-only decision.

---

## Decision

Add `external_id` columns to `projects` and `experiments`. Treat creates as upserts keyed by `(organization_id, external_id)`. Honor an `Idempotency-Key` header on all writing requests with a 24-hour cached-response replay window.

### Key components

- **Schema additions:**
  - `projects.external_id VARCHAR(255) NULL` with a partial unique index `(organization_id, external_id) WHERE external_id IS NOT NULL`.
  - `experiments.external_id VARCHAR(255) NULL` with the same partial unique index.
  - `samples.sample_id_external` keeps its existing semantics; uniqueness is scoped within `experiment_id`.
- **Upsert behavior:** if a POST includes `external_id` and a row with that `(org_id, external_id)` exists, update mutable fields and return the existing row with HTTP 200. If not, insert and return 201. Bodies without `external_id` always insert.
- **Idempotency-Key header:** clients can send `Idempotency-Key: <uuid>` on any POST or PATCH. Server stores `(api_key_id, key)` -> `(request_fingerprint, response_status, response_body, created_at, expires_at=created_at+24h)`. On replay with the same fingerprint, the cached response is returned with `Idempotency-Replayed: true`. On replay with a different fingerprint (same key, different body), the request is rejected with 422.
- **Request fingerprint:** `sha256(method + path + canonicalized_body)`. Header order is not part of the fingerprint; only the semantically meaningful request inputs are.
- **Storage:** `idempotency_keys` table with `(api_key_id, key)` unique. Cleanup runs in the existing background-task loop in `app/main.py`, sweeping expired rows.
- **Scoping:** idempotency cache is per `api_key_id`. Two different keys can each use the same idempotency-key value without colliding. This matches how Stripe scopes idempotency to the API key.
- **External_id namespace:** per-organization. Two LIMS in the same org sharing an external_id namespace collide; first writer wins. Future scope: per-`api_key_id` namespacing if collisions become a problem in practice.

### Why upsert by external_id rather than separate "create" and "get-or-create"

A LIMS that has already pushed a project does not want to track whether bioAF acknowledged the create. "POST always, server reconciles" is the simplest mental model for the integrator. The response code (201 vs 200) tells the client whether they actually inserted or matched an existing row, which is enough for any UI or log surface they care to build.

### Why a 24h replay window

Stripe sets 24h. Long enough to cover any reasonable retry loop (including a worker that wakes up after an overnight outage), short enough that the table does not grow unboundedly. Configurable per-deployment if a customer needs a different value, but 24h is the default and is documented.

### Why reject on fingerprint mismatch instead of treating as a new request

If a client sends two different bodies with the same idempotency key, that is almost always a bug on the client side (key reuse). Silently treating it as a new request hides the bug. 422 with `detail="idempotency_key_reused_with_different_body"` surfaces it.

---

## Out of scope

- Bulk endpoints. Clients loop single creates with idempotency keys per row.
- Idempotency on GET. GETs are already idempotent.
- Per-key external_id namespacing. Per-org for v1.
- Idempotency cache TTLs other than 24h. One value, documented.

---

## Consequences

### Positive

- A retry storm cannot create duplicates. The contract is simple to reason about and matches industry precedent.
- External systems can move to bioAF without surrendering their primary IDs. Mapping between systems lives in one column instead of a separate mapping table.
- Future integrations (Slack, webhook receivers wanting to confirm bioAF-side state) inherit the same idempotency model.

### Negative

- `idempotency_keys` is a write-amplification path: every create writes a row in addition to the resource row. Cleanup keeps it bounded but the table is hot.
- 422 on key reuse is a footgun the first time an integrator hits it. The error body is explicit; the OpenAPI doc calls it out.
- A LIMS that pushes the same external_id under different bioAF orgs will create two distinct projects. This is intentional (org boundary is real) but worth flagging in the integration guide.

---

## References

- [ADR-006](ADR-006-experiment-tracking-as-foundation.md) -- experiment as the primary tracking unit; external_id added to it here.
- [ADR-018](ADR-018-cross-experiment-projects.md) -- project model the external_id attaches to.
- [ADR-048](ADR-048-public-integration-api-surface.md) -- the surface these contracts apply on.
- Spec: `documentation/spec-lims-integration-api.md`
