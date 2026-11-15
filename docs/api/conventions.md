# Conventions

Common patterns shared by every resource on the public integration API.

## external_id is required on create

Every `POST` to `/projects`, `/experiments`, or `/samples` requires a
non-empty `external_id` in the request body.

- Missing `external_id` -> `422 Unprocessable Entity` (Pydantic validation).
- `external_id` already exists in the duplicate-detection scope -> `409 Conflict`
  with `{"detail": "external_id_already_exists"}`.

Duplicate scope is:

| Resource | Uniqueness scope |
| --- | --- |
| Project | `(organization_id, external_id)` |
| Experiment | `(organization_id, external_id)` |
| Sample | `(experiment_id, external_id)` |

This avoids accidental double-creates from retries or noisy LIMS sync
loops. To make a retry safe even before the first call lands, send an
`Idempotency-Key` header (see below).

## Idempotency-Key

Supported on the three create endpoints: `POST /projects`, `POST /experiments` and `POST /samples`.
PATCH handlers do not read this header. On a supported POST, if the header is present:

1. bioAF looks up `(api_key_id, Idempotency-Key)` in the idempotency cache.
2. If found and the request body fingerprint matches: the cached response
   is replayed with header `Idempotency-Replayed: true`.
3. If found and the body fingerprint differs: `422` with
   `{"detail": "idempotency_key_reused_with_different_body"}`.
4. If not found: the request runs normally. The response status and body
   are then cached for 24 hours.

Replays preserve the cached status code, including `201` for a successful create.
Use a stable, request-unique value (e.g. a UUID or a deterministic hash
of the source row). bioAF treats the key as opaque.

## Pagination (cursor)

This is the public Integration API contract. Internal `/api/experiments` instead uses `page`/`page_size`
(1-100 items per page), name search and a deleted-status filter; do not exchange pagination parameters
between the two surfaces.

List endpoints return up to `limit` items (default 50, max 200) and a
`next_cursor` (opaque string) when more are available. To fetch the next
page, pass `?cursor=<value>`. Cursors are monotonically decreasing by id;
rows are selected in descending ID order. Concurrent writes are not a snapshot of the whole result set.

```http
GET /samples?experiment_id=42&limit=50
-> 200 OK { "items": [...], "next_cursor": "12345" }

GET /samples?experiment_id=42&limit=50&cursor=12345
-> 200 OK { "items": [...], "next_cursor": null }
```

## Status writes are not permitted

`status` (experiment) and `qc_status`/`status` (sample) are bioAF-managed.
External writes are rejected at request time:

- `POST` with `status` in the body -> `400 Bad Request` with
  `status_writes_not_permitted` or `qc_writes_not_permitted`.
- `PATCH` with `status`/`qc_status` -> same.

Reads of these fields are always allowed.

## Error envelope

Errors use FastAPI's standard `{"detail": ...}` shape. Some route errors use a short snake_case code,
while authentication and other errors can use human-readable text. Do not assume every detail is a code:

```json
{ "detail": "external_id_already_exists" }
```

Validation errors (Pydantic) instead return the list-of-issues shape:

```json
{
  "detail": [
    {"loc": ["body", "external_id"], "msg": "Field required", "type": "missing"}
  ]
}
```

## Status code summary

| Code | Meaning |
| --- | --- |
| 200 | Read or update succeeded. |
| 201 | Create succeeded, or its successful cached response was replayed. |
| 400 | Forbidden field in payload (e.g. `status`, `qc_status`). |
| 401 | Missing, malformed, or revoked key. |
| 403 | Key authenticated but lacks the required scope (or SA role does). |
| 404 | Resource id or external_id not found (in this org). |
| 409 | `external_id` already used in the duplicate-detection scope. |
| 422 | Body validation failed, or idempotency key reused with different body. |
| 429 | (Future) per-key rate limit; not enforced in v1. |

## external_id vs code vs uuid

The public identity fields differ by resource:

| Field | Source | Visible | Purpose |
| --- | --- | --- | --- |
| `id` | bioAF | Yes | Internal integer primary key. Stable. |
| `code` | bioAF, auto-generated | Projects and experiments only | Human-readable, e.g. `bioap-0008`; samples have no `code` field. |
| `external_id` | External system | Yes | The LIMS-side identifier. |
| `uuid` | bioAF, auto-generated | No (internal) | System uniqueness; not part of the public surface. |

External integrators should treat `external_id` as the join key. `code`
is for humans (lab labels, notebooks). `id` is stable but bioAF-internal.
