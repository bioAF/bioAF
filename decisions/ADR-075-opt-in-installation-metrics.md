# ADR-075: Opt-in installation metrics

**Status:** Proposed
**Date:** 2026-11-15
**Deciders:** Brent (repository owner)

## Context

The bioAF team needs to know how many [Installations](../ai_guides/glossary.md#installation) exist and
how quickly they adopt new releases, for labs in the United States, Canada and the EU. bioAF runs on
customer-controlled infrastructure and has never reported anything outbound. Any reporting must leave
the lab's scientific work, users and Organization details untouched, must not affect setup, login,
upgrades or readiness, and must be the installation admin's explicit choice.

## Decision

**Explicit opt-in, enforced by the backend.** An installation reports
[Installation Metrics](../ai_guides/glossary.md#installation-metrics) only after an admin with
`infrastructure:configure`, in a human session, chooses *Share installation metrics*. Pending and
declined choices create no [Installation ID](../ai_guides/glossary.md#installation-id), no queued
report and no network traffic, including the first report. The choice is installation-wide and is
asked once: on the setup wizard's Organization Name step for a new installation, or on the first
authenticated app entry by such an admin for an installation whose choice is still pending. Either
committed choice resolves the prompt for good; later changes happen in Settings > System Settings
(`GET /api/system-settings`, `PUT /api/system-settings/install-metrics`). The mutation accepts only the
choice, the notice version (`NOTICE_VERSION`, currently `2026-11-15`) and the revision it was read at; a
stale revision or another notice version is a 409, and the backend owns IDs, dates and payloads.

**One policy owner and local consent evidence.** `InstallMetricsService` owns the versioned state in
`platform_config.install_metrics_state`: the nullable Installation ID (a random UUID created on first
opt-in, never rotated), new/existing classification (made by migration before bootstrap creates an
Organization), preference, notice version, exact decision time, the effective enabled-since time,
the operator-override state, setup completion and baseline markers, and a revision for optimistic
concurrency. Writes lock the row; a stale revision is refused. Consent evidence stays local and is
never sent; the audit log records which admin chose. Migration 133 creates the outbox and classifies the
database; setup completion (`POST /api/bootstrap/complete`, the end of both wizard paths) is recorded after
setup commits, and a metrics failure never changes the setup outcome.

**Three lifecycle events, from a closed allowlist.** `bioaf_installation_completed` (new setup
completed while reporting is enabled), `bioaf_installation_observed` (first reporting enablement
for an already-completed installation) and `bioaf_upgrade_completed` (a host upgrade operation that
completed successfully). Each carries the event UUID, the Installation ID as `distinct_id`, a
timestamp rounded to `00:00:00Z` of the UTC event day, `metrics_schema_version=1`, the official
`bioaf_version` (or `unknown`), `from_version` for upgrades, `$geoip_disable=true`, and `$set_once`
of the first-reported date and `registration_kind` (`new`, `existing`, `first_seen_on_upgrade`).
Payloads are built only from typed fields; unknown keys and invalid values are refused without
echoing them.

**A local outbox and an owned, bounded sender.** Events are reserved in `install_metric_events`
under a unique source key (`baseline`, once per installation; `upgrade:<installation id>:<operation id>`),
with the immutable payload and reporting date; a settled row keeps only its key, event UUID and status. The
periodic job `install-metrics-sender` (first after one minute, then every five) is owned by the
application's `TaskOwner`, pauses during a restore review, and sends at most 5 reports per pass with a
3-second timeout, backs off up to 24 hours, expires reports after 7
days, holds at most 100 pending upgrade reports, and never holds a database transaction across a
network call. It rechecks consent, the enabled interval and the operator override before each send.
Withdrawal or `BIOAF_INSTALL_METRICS_DISABLED=1` cancels everything pending (a startup step applies an
override changed across a restart); re-enabling never replays it.

**Direct HTTPS to PostHog, configuration in the image.** The public project token and US ingestion
host are committed in `backend/app/install_metrics_release.py`, so installers handle no token. The
paired overrides `BIOAF_POSTHOG_PROJECT_TOKEN` / `BIOAF_POSTHOG_HOST` must both be set and valid or
delivery is disabled. The sender posts to `<host>/i/v0/e/` with `httpx` through an injectable
transport.

**Host upgrade operations carry their own identity.** `./bioaf update` records an `update_operation`
(UUID, original start time and source version, target, status, completion time) in private host
state, keeps it across retries of the same unfinished target, and publishes it with completion
evidence in shared update status. After readiness and final host completion the updater runs a
backend CLI (`python -m app.cli.record_upgrade_metric`) that reserves the upgrade event without network
I/O; each sender pass reconciles the same facts from `update-status/current.json` (read without following
links, completions under 7 days old) if that call failed. Only operations that began inside the current
uninterrupted enabled interval qualify. An interrupted update whose target image already runs settled is
finished with its readiness checks rather than treated as a no-op.

## Rationale

- **Opt-in everywhere** avoids claiming the dataset falls outside personal-data laws in any region,
  and keeps consent and its evidence in the customer's own database.
- **A random UUID created at opt-in** gives stable histories without hardware fingerprints, and gives
  installations that never consent no telemetry identity at all.
- **Day-precision dates, IP discard and disabled GeoIP** minimize what the receiver stores; the
  receiver's sight of the egress address is inherent in a direct connection and is documented.
- **An outbox** decouples reporting from setup and upgrade outcomes and makes retries carry the
  identical event UUID and date for PostHog's deduplication.
- **Rejected alternatives:** an always-sent initial report (sends before consent); browser analytics
  or the PostHog browser SDK (user-level, cookie-based); aggregate-only reporting through a custom
  collector (new infrastructure to secure and operate); hardware or host fingerprints (re-identifying,
  and wrong after clone or migration); reusing `upgrade_id` or `UpgradeHistory` (absent for direct
  CLI updates, settled before readiness).

## Consequences

- Counts are best-effort observations of consenting installations, not inventory. A cancelled or
  lost baseline can leave an installation first observed through an upgrade.
- A restored backup restores identity and choice as of the backup. A deliberate clone needs the
  documented identity reset (`./bioaf reset-install-metrics-identity --confirm`), which clears the ID, the
  choice and queued reports.
- CI, development and tests must keep `BIOAF_INSTALL_METRICS_DISABLED=1` (workflow-level `env`, development
  Compose, test subprocess launchers, `tests/conftest.py`), and the suite's default sender transport refuses
  every request, so sender tests inject their own.
- Any new kind of collection needs its own consent design; this choice authorizes only these events.
- The public notice is [docs/installation-metrics.md](../docs/installation-metrics.md).
