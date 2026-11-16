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
| `experiment.created` | An Experiment was created (UI, assistant, Integration API, validation study) |
| `experiment.updated` | Experiment metadata actually changed |
| `experiment.status_changed` | An Experiment moved to a new status, including soft deletion (`deleted`) |
| `sample.created` | A Sample was created (one event per sample, bulk and imports included) |
| `sample.updated` | Sample metadata actually changed |
| `sample.qc_changed` | A Sample's QC status changed |
| `file.registered` | A new File record was committed |
| `file.ready` | A File's stored bytes were first verified available (see [Files](files.md#readiness)) |

Select the event types your receiver needs when creating or editing a subscription. Deliveries are
sent to active subscriptions of the originating organization that match the event type. Project
lifecycle events are outside this vocabulary; so are sample/file deletion and later link changes.

Events are published only after the change they describe is committed: a rolled-back change, a
rejected import row, or an Integration API idempotent replay sends nothing.

## Event Data

`data` contains exactly these fields; a field with no value is JSON `null`. `external_id` is always the
subject's own identifier; each parent external ID belongs to the parent ID beside it (see
[Conventions](conventions.md#external_id-vs-code-vs-uuid)).

| Event | `data` fields |
| --- | --- |
| `experiment.created` | `experiment_id`, `external_id`, `project_id`, `project_external_id` |
| `experiment.updated` | `experiment_id`, `external_id`, `project_id`, `project_external_id`, `changed_fields` |
| `experiment.status_changed` | `experiment_id`, `external_id`, `project_id`, `project_external_id`, `old_status`, `new_status` |
| `sample.created` | `sample_id`, `external_id`, `experiment_id`, `experiment_external_id` |
| `sample.updated` | `sample_id`, `external_id`, `experiment_id`, `experiment_external_id`, `changed_fields` |
| `sample.qc_changed` | `sample_id`, `external_id`, `experiment_id`, `experiment_external_id`, `old_qc_status`, `new_qc_status` |
| `file.registered` | `file_id`, `external_id`, `filename`, `file_type`, `source_type`, `project_id`, `project_external_id`, `experiment_id`, `experiment_external_id`, `sample_ids`, `samples` |
| `file.ready` | `file_id`, `external_id`, `ready_at` (UTC ISO 8601), `size_bytes` (verified size), `project_id`, `project_external_id`, `experiment_id`, `experiment_external_id`, `sample_ids`, `samples` |

`sample_ids` is the sorted list of linked sample IDs; `samples` lists the same samples in the same
order as `{sample_id, external_id, experiment_id, experiment_external_id}`. A file's project is its
own, or else its experiment's. An unassigned file has `null` pairs and empty lists.

**Counting rules.**

- `changed_fields` is the sorted list of fields whose stored value changed, by field name. Batch codes
  appear as `sample_batch_id`, `sequencing_batch_id`, `sequencing_batch_position`; custom fields and
  field defaults appear as the groups `custom_fields` and `field_defaults`, never their keys or values.
- Assigning a value a record already has, or equivalent custom fields, sends nothing.
- Creating a record includes its initial metadata, status, and QC: no extra updated/status/QC event.
- An experiment status change sends only `experiment.status_changed`; a QC status change only
  `sample.qc_changed`. A sample's lifecycle status is `sample.updated` with `changed_fields`
  `["status"]`; a QC notes-only edit is `sample.updated` with `["qc_notes"]` (never the note text).
- Several audited transitions in one request (an upload walking an experiment through intermediate
  statuses) each send their own event.
- `file.registered` and `file.ready` are separate events. They can be committed together, or minutes
  to hours apart when a large file is verified in the background.

**Snapshots and ordering.** Each payload describes the records at the moment of its change. Retries
and Replay resend the same body even if the records changed since. There is no ordering guarantee
between events, including a file's `file.registered` and `file.ready`; read the current record when
you need the current state.

**Delivery guarantees.** Delivery rows are durable once written, and retries and dead-lettering apply
from then. Producing the event is in-process and best effort: if the backend process dies between
committing a change and writing its delivery rows, that event is lost and is not reconstructed later
(not by Replay, and not by rerunning a command). There is no exactly-once delivery; deduplicate on the
body `id`.

## Recovered Pipeline Outputs

`./bioaf register-outputs` registers outputs a pipeline run left unregistered, even for old runs. A
newly recorded file sends `file.registered` (and `file.ready` when it can be verified immediately) with
the current time, not the run's. A file registered earlier is never announced as registered again; if
it was never made ready (it was registered before readiness existed), the command makes it ready and
sends `file.ready` once. Rerunning the command, `--force` included, then sends nothing more. The
summary counts new files registered and existing files made ready separately. The command writes delivery rows itself and exits nonzero, naming the affected
files, if any notification could not be persisted; the files stay registered. No matching subscription
is not an error. A file whose checksum needs a full read is verified later by the bioAF server, which
then sends `file.ready`. Receivers are contacted only by the server.

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
  "data": {"experiment_id": 7, "external_id": "LIMS-EXP-042", "project_id": 42, "project_external_id": "LIMS-PRJ-1"}
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
