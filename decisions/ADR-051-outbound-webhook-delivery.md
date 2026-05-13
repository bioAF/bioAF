# ADR-051: Outbound Webhook Delivery

**Status:** Accepted
**Date:** 2026-05-13
**Deciders:** Brent (repository owner)

---

## Context

External LIMS systems need to react to bioAF-side changes without polling: a sample registered through the UI, an experiment status transition, a file finishing upload. Today bioAF has an internal pub/sub via `app/services/event_bus.py` and a `NotificationRouter` that fans out to Slack and email per [ADR-010](ADR-010-notification-system.md). That system is purpose-built for internal user notifications, not for external HTTP delivery: payloads are shaped for human consumption, there is no retry-with-backoff persistence layer, and there is no public event vocabulary an integrator can rely on across releases.

The public webhook surface needs:

- A stable event vocabulary integrators can write code against.
- HMAC-signed payloads so receivers can verify origin.
- At-least-once delivery with retries and a dead-letter state, persisted across pod restarts.
- Parity: the same event fires whether the source path is the UI handler or the integration API handler.

---

## Decision

Add a per-organization webhook subscription model. Persist every delivery attempt in `webhook_deliveries`. A background asyncio worker drains the table, posting signed payloads to subscribed URLs with exponential backoff and a dead-letter terminal state.

### Key components

- **Subscription model:** `webhook_subscriptions` table. Per-org. Admin-managed via Settings > Users and Accounts. Each row has a URL, friendly name, list of subscribed event types, HMAC secret, and `is_active`. Secret stored encrypted via `EncryptedString` ([ADR-047](ADR-047-data-at-rest-encryption.md)).
- **Delivery model:** `webhook_deliveries` table. One row per `(subscription, event)` pair. Columns include `status` (`pending`, `delivered`, `failed`, `dead_letter`), `attempt_count`, `next_attempt_at`, `last_response_status`, `last_response_body` (truncated to 4KB), `payload_json` (full envelope as sent).
- **Event envelope (every payload):**
  ```json
  {
    "id": "evt_01H...",        // ULID; receivers dedupe on this
    "event": "experiment.created",
    "occurred_at": "2026-05-13T18:00:00Z",
    "organization_id": 42,
    "data": { ... }
  }
  ```
- **Headers:**
  - `X-bioAF-Event: <event_type>`
  - `X-bioAF-Delivery: <delivery_id>`
  - `X-bioAF-Signature: t=<unix_ts>,v1=<sha256(t + "." + body)>` keyed by the subscription secret.
- **Dispatcher:** subscribes to the internal `event_bus` for the v1 event types, translates internal event names to public ones, and inserts one delivery row per matching active subscription in `pending` state with `next_attempt_at=now()`.
- **Worker:** `app/services/webhook_worker.py`, started in `app/main.py` lifespan. Tight loop: `SELECT ... FROM webhook_deliveries WHERE status='pending' AND next_attempt_at <= now() ORDER BY next_attempt_at LIMIT 50 FOR UPDATE SKIP LOCKED`. For each row, POST via `httpx.AsyncClient(timeout=10)`. On 2xx mark `delivered`. Otherwise increment `attempt_count`, set `next_attempt_at` to `now() + backoff[attempt_count]`. After the fifth failure, set `status='dead_letter'`.
- **Retry schedule:** backoffs (seconds): `[60, 300, 1800, 7200, 43200]` (1m, 5m, 30m, 2h, 12h). Five total attempts before dead-letter.
- **v1 event vocabulary:**
  - `experiment.created`, `experiment.updated`, `experiment.status_changed`
  - `sample.created`, `sample.updated`, `sample.qc_changed`
  - `file.registered`, `file.ready`
- **No project events in v1.** Adding them later is additive; clients that subscribe to a non-existent event today simply receive nothing.
- **Replay:** admins can re-queue any delivery (including dead-lettered ones) from the UI. Replay clones the row into a fresh `pending` delivery.
- **Test event:** admins can fire a synthetic `webhook.test` delivery against any subscription to validate the receiver during setup.

### Multi-pod safety

`FOR UPDATE SKIP LOCKED` makes the worker poll safe to run on every backend pod. No leader election. If a pod dies mid-attempt, the row stays at its current `next_attempt_at` and another pod picks it up at the next poll tick. Worst case is a duplicate POST to the receiver, which is exactly the at-least-once contract the receiver must already handle (envelope `id` makes that trivial).

### Why not synchronous delivery inside the request

A user-driven UI action should not block on an outbound HTTP call to a customer-controlled URL. Persisting to `webhook_deliveries` and having the worker drain it cleanly separates the originating action's latency from the delivery latency, and survives pod restarts.

### Why bridge internal event names instead of renaming them

Internal events like `DATA_UPLOADED` and `EXPERIMENT_STATUS_CHANGED` are used by `NotificationRouter` and other consumers. Renaming them globally would touch a wide blast radius. The dispatcher translates internal names to the public vocabulary in one place, leaving internal consumers untouched.

---

## Out of scope

- Inbound webhooks (receivers calling bioAF). Inbound flows go through the public API.
- Per-subscription rate limiting beyond the retry budget.
- Filter expressions on payload fields ("only experiments where project_id=X"). v1 filters on event type only.
- Project lifecycle events.
- Per-event payload version negotiation. Single payload shape per event in v1.

---

## Consequences

### Positive

- A stable, signed, replayable event stream is the table-stakes counterpart to a sync REST API. Integrators can build event-driven workflows without polling.
- Dead-letter visibility surfaces real receiver problems instead of silently retrying forever.
- The dispatcher pattern keeps emission parity automatic: the moment a service path emits the internal event, the public webhook fires too. No double-bookkeeping for UI vs API paths.

### Negative

- A new background worker to operate. Adds a moving part to lifespan startup. Mitigated by the existing pattern of background tasks in `app/main.py`.
- Customer-controlled URLs are an egress surface. Same profile as existing notification integrations; no new GCP infra. A misconfigured URL (internal IP, malformed host) will retry five times before dead-lettering. Acceptable; the dead-letter state is the safety net.
- Event vocabulary is a long-term commitment. Renaming `experiment.created` later is a v2 break. The set was kept intentionally small to limit how much we have to commit to.

---

## References

- [ADR-010](ADR-010-notification-system.md) -- internal notification system; webhook delivery is distinct from it.
- [ADR-046](ADR-046-pipeline-version-cascade.md) -- pipeline events are part of `file.ready` semantics.
- [ADR-047](ADR-047-data-at-rest-encryption.md) -- subscription secrets encrypted at rest.
- [ADR-048](ADR-048-public-integration-api-surface.md) -- the integration surface webhooks complement.
- Spec: `documentation/spec-lims-integration-webhooks.md`
