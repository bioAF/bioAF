# Webhooks

Subscriptions are managed under **Settings > Users & Accounts > Webhooks**. The backend maps selected
internal events to public webhook names and persists deliveries for active matching subscriptions.

## Allowed Destinations

A subscription URL must be an absolute `https://` URL with a host and valid port, without user
information (`user:pass@`) or a fragment. The host is resolved when the subscription is created or its
URL changed, when it is reactivated, when an admin sends a test or replays a delivery, and again on
every delivery attempt. Every address it resolves to must be public. Loopback, private, link-local
(including cloud metadata), shared, multicast, reserved, and known IPv4-translation addresses are refused.
A private-network LIMS needs an operator exception; see
[Webhook Destination Settings](../configuration.md#webhook-destination-settings).

bioAF connects only to the address it authorized, keeps the original hostname for TLS and the `Host`
header, verifies the certificate, ignores proxy environment variables, and does not follow redirects:
a `3xx` response is an ordinary failed attempt. Receiver response bodies are never read or stored.

A refused change is rejected with `422` and leaves the subscription as it was; a resolver failure is
rejected the same way and can be retried. Disabling a subscription is always allowed.

Subscriptions created before these checks are checked on every attempt too. A delivery to a
destination that is malformed or no longer allowed makes no request and fails at once (status `failed`,
no further attempts). Correct the URL, then Replay the delivery.

Deliveries record a safe diagnostic code instead of the receiver's body or a raw error:
`invalid_destination`, `destination_denied`, `dns_failed`, `tls_failed`, `request_timeout`,
`transport_failed`, or `http_non_2xx` (with the response status code).

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
failure schedules another attempt; a refused destination does not (see
[Allowed Destinations](#allowed-destinations)). Each attempt has a 10-second total deadline covering
resolution, connection, TLS, sending, and the response headers, and the normal five-attempt cap uses
retry delays of 60, 300, 1800 and 7200 seconds after the initial attempt. A fifth failure becomes
`dead_letter`.

Subscribers can see duplicate events after ambiguous responses. Return promptly, make processing
idempotent and use the stable body event ID. Admin Replay creates a new pending delivery of the original
event. A missing/inactive subscription prevents delivery; repeated failures do not automatically disable
an otherwise active subscription.
