# Webhooks

Subscriptions are managed under **Settings > Users & Accounts > Webhooks**. The backend maps selected
internal events to public webhook names and persists deliveries for active matching subscriptions.

## Event Types

| Event | Meaning |
| --- | --- |
| `experiment.created` | An Experiment is created |
| `experiment.updated` | Experiment metadata changes |
| `experiment.status_changed` | An Experiment moves to a new status |
| `sample.created` | A Sample is created |
| `sample.updated` | Sample metadata changes |
| `sample.qc_changed` | A Sample's QC status changes |
| `file.registered` | A file is registered in bioAF |
| `file.ready` | A registered file becomes ready for use |

Select the event types your receiver needs when creating or editing a subscription. Deliveries are
sent to active subscriptions that match the event type. Project lifecycle events are outside this
event vocabulary.

## Delivery Format and Identity

```http
POST <subscriber-url>
Content-Type: application/json
X-bioAF-Event: experiment.created
X-bioAF-Delivery: 123
X-bioAF-Signature: t=1747244400,v1=ab12cd34...
```

A representative body is:

```json
{
  "id": "evt_3f1d7a92...",
  "event": "experiment.created",
  "occurred_at": "2026-05-14T15:00:00Z",
  "organization_id": 1,
  "data": {"experiment_id": 7, "external_id": "LIMS-EXP-042", "project_id": 42}
}
```

`X-bioAF-Delivery` is the integer database delivery-row ID. Automatic retries retain that row ID, but an
admin Replay clones a new row and changes the header. The body `id` identifies the event and is preserved
on Replay. Deduplicate on the body `id`, scoped to your receiving consumer/subscription, if retries and
manual replays must not repeat side effects. Use the header for delivery-attempt diagnostics.

## Verify the Signature

The signed bytes are `<unix-seconds>.<raw-body-bytes>`, using the subscription secret as the HMAC-SHA256
key. Parse the timestamp and signature from `X-bioAF-Signature`, compute the expected digest and compare
with a constant-time comparison such as `hmac.compare_digest`.

Enforce a timestamp-age window and deduplicate event IDs. A valid signature authenticates bytes; it does
not by itself reject a replay. The body must be used as received, before JSON reserialization.

## Retries and Replay

The durable worker sends one HTTP request per recorded attempt. Any HTTP 2xx is success; non-2xx or network
failure schedules another attempt. The request timeout is 10 seconds, and the normal five-attempt cap uses
retry delays of 60, 300, 1800 and 7200 seconds after the initial attempt. A fifth failure becomes
`dead_letter`.

Subscribers can see duplicate events after ambiguous responses. Return promptly, make processing
idempotent and use the stable body event ID. Admin Replay creates a new pending delivery of the original
event. A missing/inactive subscription prevents delivery; repeated failures do not automatically disable
an otherwise active subscription.
