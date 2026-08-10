# ADR-010: Built-in Notification System with Email and Slack Connectors

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF manages long-running processes (pipeline runs, cluster provisioning), background operations (backups), and cost-sensitive resources (autoscaling compute). Users need to be notified when things complete, fail, or require attention: even when they're not looking at the dashboard.

Options considered:

1. **In-app notifications only:** Insufficient. Users aren't always looking at the UI.
2. **Email only:** Universal reach but slow. Not ideal for urgent alerts.
3. **Slack only:** Fast but not everyone uses Slack. Can't deliver invitation/verification emails.
4. **Email + Slack + in-app:** Covers all bases. Email for universal delivery and auth flows. Slack for real-time team awareness. In-app for notification of record.
5. **Third-party notification service (PagerDuty, Opsgenie):** Over-engineered for our target users. Adds cost and operational dependency.

## Decision

bioAF ships with three built-in notification channels: in-app (always enabled), outbound email (SMTP-based), and Slack (webhook-based). No external notification service dependencies.

### Architecture

The notification system is an internal service within the bioAF control plane:

```text
Platform Events → Event Bus → Notification Router → Channel Adapters
                                    │                    ├─ In-App (write to DB)
                                    │                    ├─ Email (SMTP)
                                    │                    └─ Slack (webhook POST)
                                    │
                              Notification Rules
                              (per-event routing config)
```

**Event Bus:** Internal (in-process) pub/sub within the FastAPI application. Platform events are emitted by service handlers (pipeline complete, backup failure, etc.) and consumed by the notification router. Not an external message broker: this is a simple observer pattern.

**Notification Router:** Reads the notification rules configuration to determine which events trigger which channels for which recipients. Rules are configurable by admin and per-user preferences are respected.

**Channel Adapters:**

- **In-app:** Writes to a `notifications` table in Postgres. The UI polls or uses WebSockets for real-time badge updates.
- **Email:** Sends via SMTP using credentials from Secret Manager. Admin configures SMTP settings during bootstrap (or later). Zero-config fallback available for low-volume alerts.
- **Slack:** Sends formatted messages via webhook POST. Admin provides webhook URL(s) in settings. Supports channel routing (e.g., pipeline alerts to #bioaf-pipelines, cost alerts to #bioaf-admin).

### Configuration Layers

1. **Admin-level:** Configure SMTP, Slack webhooks, and org-wide default notification preferences per role. Set mandatory alerts (e.g., all admins must receive backup failure alerts via email).
2. **User-level:** Each user can override default preferences: which alert types they receive, via which channels. Cannot disable admin-mandated alerts.

## Rationale

- **Email is required regardless.** bioAF needs email delivery for user invitations and password verification codes. Once SMTP is configured for auth, using it for notifications adds zero incremental infrastructure.
- **Slack is where biotech teams communicate.** Adding a Slack connector is low effort (HTTP POST to a webhook URL) and high value.
- **In-app is the notification of record.** Even if email or Slack fail, the in-app notification persists.
- **No external dependencies.** No PagerDuty, no SNS, no third-party notification SaaS. The entire system runs within the bioAF control plane using SMTP and HTTP POST.
- **Simple architecture scales to our needs.** A small biotech generates at most dozens of notifications per day. An in-process event bus is more than sufficient: no Kafka, no Redis pub/sub, no external queue.

## Consequences

- SMTP configuration is part of the admin bootstrap wizard. If SMTP isn't configured, user invitations must fall back to manual invite links (copy-paste URL). The UI should make this clear.
- The `notifications` table in Postgres will grow over time. A retention policy (e.g., delete read notifications older than 90 days) should be implemented.
- Failed email/Slack deliveries should be retried (2-3 attempts with backoff) and logged. Persistent delivery failures should trigger an in-app alert to admins.
- The notification rules configuration should be stored in the GitOps repo as `config/notification-config.json` for version control and rollback.
- Future notification channels (Microsoft Teams, webhooks to arbitrary URLs) can be added as additional channel adapters without changing the event bus or router architecture.
