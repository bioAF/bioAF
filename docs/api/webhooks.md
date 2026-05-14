# Webhooks

bioAF posts JSON events to subscriber URLs when entities change. Each
delivery is signed; failed deliveries are retried with exponential
backoff and eventually dead-lettered.

Webhook subscriptions are managed in Settings > Users & Accounts >
Webhooks. There is no public endpoint to create or modify subscriptions.

## Event catalog

| Event | Fires when |
| --- | --- |
| `experiment.created` | A new experiment is created (any source). |
| `experiment.updated` | An experiment's mutable metadata changes. |
| `experiment.status_changed` | An experiment moves between status states. |
| `sample.created` | A new sample is registered. |
| `sample.updated` | A sample's mutable metadata changes. |
| `sample.qc_changed` | A sample's QC status changes. |
| `file.registered` | bioAF records a new file row. |
| `file.ready` | A file is fully ingested and downstream tasks can use it. |

There are no project lifecycle events in v1.

## Delivery format

```http
POST <subscriber-url>
Content-Type: application/json
X-bioAF-Event: experiment.created
X-bioAF-Delivery: evt_3f1d7a92...
X-bioAF-Signature: t=1747244400,v1=ab12cd34...
```

Request body:

```json
{
  "id": "evt_3f1d7a92...",
  "event": "experiment.created",
  "occurred_at": "2026-05-14T15:00:00Z",
  "organization_id": 1,
  "data": {
    "experiment_id": 7,
    "external_id": "LIMS-EXP-042",
    "project_id": 42
  }
}
```

The `data` payload is event-specific and intentionally minimal. To get
the full row, call the appropriate read endpoint (`/experiments/{id}`,
`/samples/{id}`, `/files/{id}`).

## Verifying the signature

`X-bioAF-Signature` has the form `t=<unix-seconds>,v1=<hex-sha256>` where
the signed message is `<unix-seconds>.<raw-body-bytes>` and the key is
the subscription's secret (shown once at create time, regenerable via
the rotate-secret action).

Pseudocode:

```python
import hmac, hashlib, time

t, v1 = parse_signature(request.headers["X-bioAF-Signature"])
message = f"{t}.".encode() + request.body
expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, v1)
# Optional: reject if abs(time.time() - int(t)) > 300
```

`hmac.compare_digest` (or your language's equivalent) avoids timing
attacks. Anchoring the timestamp inside the signed message blocks replay.

## Delivery semantics

- At-least-once. Subscribers must dedupe on `X-bioAF-Delivery` (also
  available as `id` in the body).
- Success: any HTTP 2xx from the subscriber within the timeout.
- Retry: any 4xx/5xx (including network errors) is retried.
- Backoff schedule (seconds since the previous attempt): **60, 300, 1800,
  7200, 43200**.
- Max attempts: **5**. After the fifth failure the delivery is moved to
  `dead_letter` status and stops retrying.
- Replay: an admin can re-queue any delivery (including `dead_letter`)
  from the Webhooks > delivery row.

## Operational responses

- `2xx`: the subscriber received the event. Body is ignored.
- `4xx`/`5xx`: retry per schedule above. Subscribers should respond fast
  (target under 5s) to avoid timeouts.
- The subscription is **not** auto-disabled on repeated failures in v1.
  Operators can disable a subscription from the UI.

## Inspecting deliveries

Settings > Users & Accounts > Webhooks shows the deliveries table for
each subscription with status, attempt count, last response status, and
a Replay button per row.
