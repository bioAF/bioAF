# ADR-064: Date-Based SDR Re-Assessment Triggers

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

Scientific decisions have a shelf life. A threshold set when the lab had 10 experiments may need
revisiting at 100. Without a trigger mechanism, SDRs (ADR-059, ADR-063) go stale unnoticed.

The template assumed a generic "background scheduler" and floated APScheduler / Celery beat /
K8s CronJob. The codebase uses none of these. Recurring work runs as long-lived `asyncio` loops
launched in the FastAPI lifespan in `backend/app/main.py` (for example `_review_reminder_loop`
on a 6-hour tick and `_cost_billing_sync_loop` on a 24-hour tick), each wrapped in
try/except with `asyncio.sleep(interval)`. There is no notification-deduplication mechanism in the
notification system (`InAppChannel.deliver`), so "send a warning exactly once" must be tracked in
data.

## Decision

SDRs support an optional date-based re-assessment trigger (`trigger_date`). Evaluation runs as a
new daily `asyncio` loop, `_sdr_trigger_loop()`, registered in the `main.py` lifespan alongside
the existing loops (24-hour tick), delegating to an `sdr_service` function so the logic is unit
testable without the loop. It is **not** a K8s CronJob, APScheduler job, or Celery beat task.

Each daily run scans `scientific_decision_records` where `status = 'active'` and
`trigger_date IS NOT NULL`:

1. **7-day advance warning** (`now() < trigger_date <= now() + 7 days`): send an in-app
   notification to `owner_user_id` via `InAppChannel.deliver(...)`. No status change. To satisfy
   "sent once" (AC-C06) without a notification-dedup mechanism, a `trigger_warning_sent_at`
   timestamp column on `scientific_decision_records` is set when the warning fires and checked to
   prevent re-sending on subsequent daily runs.
2. **Trigger reached** (`trigger_date <= now()`): transition `active -> flagged_for_review`
   (system-initiated, through the ADR-063 service-layer transition guard), write an
   `sdr_status_transitions` row with a system note and an `audit_log` entry, and notify
   `owner_user_id`.

The trigger is optional; SDRs without a `trigger_date` stay active until manually transitioned.
Event-based triggers (platform events, experiment-count thresholds) are deferred to v2.

## Rationale

Using a `main.py` `asyncio` loop matches every other recurring task in the codebase and avoids
introducing a scheduler dependency the platform does not otherwise have. Delegating the actual
work to an `sdr_service` function (rather than burying it in the loop) keeps it testable: tests
call the function directly with a controlled clock and assert the transition, the audit row, and
the single notification. A `trigger_warning_sent_at` column is the simplest correct way to
guarantee once-only warnings given the absence of notification dedup, and it is the approach the
spec itself identified as sufficient.

## Consequences

**Positive:**

- Labs are prompted to revisit decisions without relying on memory or calendar reminders.
- The status machine does the work: SDRs visibly become `flagged_for_review` rather than carrying
  a silent overdue badge.
- Reuses the existing recurring-task pattern and notification delivery; no new infrastructure.

**Negative:**

- Date-based triggers require predicting when a decision should be revisited, which is not always
  knowable upfront (event-based triggers, deferred to v2, would address this).
- The daily loop runs in the API process; a process restart skips at most one tick, which is
  acceptable for a daily re-assessment cadence and self-heals on the next run.
- `trigger_warning_sent_at` must be cleared if a trigger cycle restarts (e.g. an upheld SDR gets a
  new future `trigger_date`), so the next cycle's warning can fire; this reset is handled in the
  service when `trigger_date` changes.
