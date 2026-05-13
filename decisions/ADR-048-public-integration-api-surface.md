# ADR-048: Public Integration API Surface

**Status:** Accepted
**Date:** 2026-05-13
**Deciders:** Brent (repository owner)

---

## Context

bioAF today is a minimal LIMS with a UI plus JWT-authenticated REST endpoints under `/api/*`. Every existing route assumes a logged-in user JWT. Customers who run bioAF alongside another LIMS (Benchling, LabKey, in-house tooling) need projects, experiments, samples, and file metadata to flow between systems without manual re-keying, and without piggy-backing on a user's interactive session.

There is no public, key-authenticated surface today. The existing `/api/*` routes are not contract-stable: they are shaped to serve the UI, return whatever the UI happens to want, and change freely with the frontend. They are also gated behind `docs_url=None, openapi_url=None` in production following a pentest finding that flagged the open `/docs` endpoint.

A public integration surface needs:

- A stable contract that does not move with UI iteration.
- An OpenAPI document external developers can read.
- A versioned URL prefix so we can ship a `v2` without breaking `v1` clients.
- Mechanical isolation from the internal API so we cannot accidentally expose internal handlers.

---

## Decision

Mount a separate FastAPI sub-app at `/api/v1/integrations`. The sub-app owns its own OpenAPI document, security scheme, and lifecycle. The main app's `/openapi.json` and `/docs` stay disabled in production; the sub-app's `/openapi.json` and `/docs` are enabled in production unconditionally.

### Key components

- **Mount point:** `app.mount("/api/v1/integrations", integrations_app)` in `app/main.py`. The sub-app is a `FastAPI(title="bioAF Integration API", version="1.0", openapi_url="/openapi.json", docs_url="/docs", redoc_url=None)`.
- **Path versioning:** path-based (`/api/v1/integrations/*`). New major versions get a new prefix (`/api/v2/integrations/*`) and a new sub-app. Minor/additive changes ship under the existing prefix.
- **Resource catalog (v1):** projects, experiments, samples, file metadata. Read-only for files; full CRUD-minus-delete for the others. No bytes flow through this surface in v1: file uploads and downloads stay on the existing internal automated pipeline.
- **Auth scheme:** HTTP Bearer with API keys minted per [ADR-049](ADR-049-service-accounts-and-api-keys.md). Service account JWTs are never issued.
- **OpenAPI exposure:** the sub-app's `/openapi.json` is reachable by any unauthenticated client; only the operations themselves require auth. Treating the schema as public lets API consumers generate clients without provisioning a key first, and the schema does not contain customer data.
- **Contract stability:** within a major version, response shapes are additive only. Fields are not renamed and not removed. New fields may be added; clients must ignore unknown fields.
- **Status writes are not public:** experiment status and sample QC status are bioAF-managed; the public API rejects `status` and `qc_status` in writes. Status is readable.

### Why a sub-app vs. a router under the main app

- A sub-app has its own OpenAPI document and middleware stack. We can keep `docs_url` off on the main app while turning it on for integrations without conditional path filtering.
- A separate app makes it mechanically harder to leak internal handlers: the integrations sub-app is wired only to integration routers, with its own auth middleware that only accepts API keys.
- Future v2 launches happen by mounting a second sub-app, not by editing routers in place.

---

## Out of scope

- File upload or download through the public API in v1. Bytes continue to flow only through the automated pipeline. Webhooks tell LIMS systems when a file appears; metadata is fetchable.
- Public project lifecycle events (out of scope for v1 webhooks per [ADR-051](ADR-051-outbound-webhook-delivery.md)).
- Per-key rate limiting; v1 uses the existing path-based rate limiter unchanged.
- mTLS, IP allowlists. Bearer tokens only.
- Bulk endpoints. Clients loop single creates with `Idempotency-Key` per [ADR-050](ADR-050-external-ids-and-idempotent-writes.md).

---

## Consequences

### Positive

- External integrators get a stable, documented contract that does not drift with UI changes.
- A clear blast-radius boundary: the sub-app cannot reach internal-only handlers because those handlers are not mounted under it.
- OpenAPI publishing reverses the prior "docs off in production" default only for the contract we are willing to commit to long-term.

### Negative

- Two FastAPI apps to wire (middleware, error handlers, lifespan hooks coordinated through the parent). The webhook worker and audit pipeline live in the main app lifespan; the sub-app does not duplicate them.
- A second OpenAPI document increases the documentation surface customers can ask questions about. Acceptable: that surface is exactly what they integrate against.
- Public-facing OpenAPI document is fetchable without a key. Anyone can enumerate the endpoint list. This is by design (clients need it to code-gen), and the endpoints themselves still require valid auth.

---

## References

- [ADR-029](ADR-029-signed-url-direct-upload.md) -- signed-URL direct upload remains internal-only in v1; the public API does not expose the upload flow.
- [ADR-049](ADR-049-service-accounts-and-api-keys.md) -- identity and auth for this surface.
- [ADR-050](ADR-050-external-ids-and-idempotent-writes.md) -- idempotent writes and external_id upsert.
- [ADR-051](ADR-051-outbound-webhook-delivery.md) -- event emission for the same resources.
- Spec: `documentation/spec-lims-integration-api.md`
- Spec: `documentation/spec-lims-integration-overview.md`
