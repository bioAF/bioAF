# Installation Metrics

bioAF can share a small set of optional installation metrics with the bioAF team. Nothing is sent unless an
installation admin chooses to share them. This page is the public notice for that choice:
what is sent, why, to whom, how long it is kept, and how to stop or request deletion.

The design is recorded in [ADR-075](../decisions/ADR-075-opt-in-installation-metrics.md).

## Purpose

The bioAF team uses installation metrics to count consenting installations over time and to see how
quickly installations adopt new releases. They are not used for advertising, marketing, user analytics,
licensing or billing, and they are not combined with support, billing, GitHub, SaaS or identity data.

## Your choice

- **New installations** see the choice on the setup wizard's Organization Name step. Neither option is
  preselected, and the step cannot continue until one is chosen. Both choices lead to the same setup.
- **Existing installations** (set up before this feature, or set up without making the choice) ask the first
  admin with the `infrastructure:configure` permission who enters the app, with **Share**, **Don't share** and
  **Decide later**. Decide later, or closing the dialog, leaves the choice undecided; it is asked again in a
  later session.
- Once either choice is saved, the dialog never appears again for this installation, for any admin, browser
  or release. Change the choice at any time in **Settings > System Settings**.
- The choice applies to the whole installation, and only a person signed in with an admin session can make
  it. API keys and service accounts cannot.
- Choosing **Do not share** sends nothing at all, including the initial report. bioAF works the same either
  way.

The choice, the notice version it was made against, the exact time it was made, and which admin made it are
recorded in the installation's own database and audit log. None of that is sent.

## What a report contains

Each report is one of three events:

| Event | When |
| --- | --- |
| `bioaf_installation_completed` | Setup finishes on a new installation whose admin chose to share. |
| `bioaf_installation_observed` | An admin first chooses to share on an installation whose setup was already complete. Only the current date is reported, never earlier installation or upgrade history. |
| `bioaf_upgrade_completed` | `./bioaf update` (or the in-app update) finishes a platform upgrade, including the database upgrade and readiness checks, after sharing was turned on. |

Every report contains exactly these fields and nothing else:

| Field | Value |
| --- | --- |
| `event` | One of the three event names above |
| `uuid` | A random ID for the report, reused only when the same report is retried |
| `distinct_id` | The **installation ID**: a random UUID created the first time an admin chooses to share |
| `timestamp` | The reporting date in UTC, at `00:00:00Z` (day precision) |
| `metrics_schema_version` | `1` |
| `bioaf_version` | The official release version, or `unknown` for a custom or development build |
| `from_version` | Upgrades only: the previous official release version, or `unknown` |
| `$geoip_disable` | `true`, so no location lookup is made |
| `$set_once.first_reported_date` | The date of the first report PostHog receives for this installation |
| `$set_once.registration_kind` | `new`, `existing` or `first_seen_on_upgrade`: how the installation was first observed |

The two `$set_once` values form a minimal profile for the installation ID. A later report never replaces
them. `registration_kind` describes how an installation was first observed, not when it was installed.

## What is never sent

Reports never contain names, email addresses, usernames, Organization details, domains, host names, cloud
accounts or projects, IP address properties, device fingerprints, cookies, request headers, URLs, logs,
credentials, or any scientific content or metadata (experiments, projects, samples, files, paths, notebooks,
pipelines, parameters, literature, results or error text). Reports are built from a fixed list of fields,
and anything outside it is refused before sending.

The installation ID is random. It is not derived from hardware, the host, the network or the Organization.
It is pseudonymous rather than anonymous: it links one installation's reports to each other.

## Where reports go

**Responsible party:** bioAF (the bioAF team), reachable at [privacy@bioaf.co](mailto:privacy@bioaf.co).

Reports go directly from the bioAF backend to **PostHog** (US Cloud, `https://us.i.posthog.com`), which
processes them for the bioAF team. bioAF uses no browser analytics, session replay, cookies, feature flags
or advertising integrations for this feature.

**Network metadata.** Like any HTTPS connection, PostHog sees the server's egress IP address and the arrival
time to handle the request. The bioAF project is configured to discard IP addresses from stored events and
profiles, and location enrichment is disabled (`$geoip_disable`). Rounding the report date does not remove
this connection metadata. PostHog's own operational logs are governed by its
[data processing agreement](https://posthog.com/dpa) and [privacy controls](https://posthog.com/docs/privacy/data-collection).

## Retention

- **Reports** (events) are kept for 12 months, then deleted by PostHog's retention. PostHog may take some
  time after that to remove expired events.
- **The installation profile** (the installation ID with its `first_reported_date` and `registration_kind`) is
  kept until a deletion request, so that installations can be counted across releases. It holds nothing
  beyond those values.
- The bioAF team keeps no exports or copies of reports outside PostHog.

## Turning reporting off and deleting reports

- **Withdraw** by choosing **Do not share** in Settings > System Settings. New reports stop immediately and
  every report still waiting to be sent is cancelled. A request already in flight may still complete.
- **Re-enable** at any time. The installation keeps the same installation ID. Nothing from while reporting
  was off is sent later, and a cancelled report is never re-sent.
- **Deletion.** Turning reporting off does not erase reports already received. To have them removed:
  1. Turn reporting off in **Settings > System Settings**.
  2. Email [privacy@bioaf.co](mailto:privacy@bioaf.co) with your installation ID (shown in Settings > System
     Settings).
  3. The bioAF team verifies the request, deletes the events and profile for that installation ID in PostHog,
     and confirms by email when it is done.

  Your contact details are used only to handle the request and are never added to the metrics.

## Operator controls

| Host setting (`docker/.env`) | Effect |
| --- | --- |
| `BIOAF_INSTALL_METRICS_DISABLED=1` | Turns reporting off whatever the admin chose, and cancels pending reports. Removing it later sends nothing from while it was set. System Settings shows that it is active. |
| `BIOAF_POSTHOG_PROJECT_TOKEN` and `BIOAF_POSTHOG_HOST` | Replace the destination built into the release image. Both must be set, the host must be an `https://` origin, and the token a PostHog project token; otherwise delivery is disabled. |

The backend image carries the release's public project token, which can only submit events and cannot read
data back. Installers need no token configuration. Restart the backend after changing these settings.

## Delivery

Reports are queued in the installation's database and sent by the backend in the background: at most five
per pass, each with a 3-second timeout, retried with backoff of up to 24 hours, and discarded unsent after 7
days. No more than 100 upgrade reports wait at once. A delivery failure, quota limit or missing
configuration never affects setup, sign-in, upgrades or readiness; it can only mean a report is not
received. Counts are therefore observations of consenting installations, not a complete inventory.

## Backups and copies

A database restore brings back the installation ID and choice as they were when the backup was taken.
If you deliberately copy an installation and keep both running, give the copy its own identity on the copy's
host:

```bash
./bioaf reset-install-metrics-identity --confirm
```

This clears the installation ID, the saved choice and every queued report. The next admin is asked again,
and choosing to share creates a new installation ID. bioAF does not detect copies automatically.
