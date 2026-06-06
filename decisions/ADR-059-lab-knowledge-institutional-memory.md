# ADR-059: Lab Knowledge as Institutional Memory Layer

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

bioAF has no structured home for non-experimental institutional knowledge. Operational
documents (equipment manuals, lab policies, cleaning schedules), lab-specific terminology,
and the reasoning behind scientific decisions all live in people's heads, scattered Google
Docs, or Slack threads. This knowledge walks out the door when team members leave, creates
onboarding friction, and is invisible to regulatory review.

The existing `documents` feature (`backend/app/services/document_service.py`, surfaced under
Data & Files) is experiment/sample-linked: a `Document` row carries `linked_experiment_id`
and `linked_sample_id` and is intended for scientific artifacts (protocols, papers, SOPs).
It is not appropriate for operational content. There is no glossary feature and no analog
to the ADR pattern for scientific decisions.

## Decision

Introduce a new top-level platform section, Lab Knowledge, with three components:

1. **Lab Documents:** versioned, tag-organized storage for operational and institutional
   documents not tied to experiments.
2. **Lab Glossary:** a governed dictionary of lab-specific terminology, with AI-assisted
   population and mandatory human review of all AI proposals (see ADR-062).
3. **Scientific Decision Records (SDRs):** structured records of scientific decisions with
   justifications, statuses, and optional re-assessment triggers (see ADR-063, ADR-064).

Lab Knowledge coexists with the existing experiment-linked `documents` feature. It does not
replace or absorb it. It surfaces as a new top-level nav section in `frontend/src/lib/navConfig.ts`
with sub-routes Documents, Glossary, and Decision Records under `frontend/src/app/lab-knowledge/`.

All three components integrate with the platform's existing cross-cutting systems rather than
introducing new infrastructure:

- **RBAC** via the custom permission system (ADR-032): permissions are `(resource, action)`
  tuples registered in `backend/app/services/bootstrap_roles.py` (`ALL_RESOURCES_ACTIONS`,
  `BUILTIN_ROLES`) and enforced with `require_permission(resource, action)` from
  `backend/app/api/dependencies.py`. New permissions are backfilled to existing system roles
  via an Alembic data migration, mirroring `alembic/versions/071_add_references_resource_permissions.py`.
- **Audit log** (ADR-009) via `audit_service.log_action(session, user_id, entity_type, entity_id, action, details=, previous_value=)`,
  called in the same transaction as the state change.
- **Global search** via the existing quick/full search surface, with new `entity_type`
  discriminators wired into `frontend/src/lib/searchLinks.ts`.

**Cross-cutting reconciliation (integer primary keys).** The spec data model specified
`UUID PRIMARY KEY` for all Lab Knowledge tables. The codebase convention is an integer
autoincrement primary key (optionally plus a `uuid` column defaulted to `gen_random_uuid()`),
and `audit_log.entity_id` is an `Integer`. To integrate with the audit log and existing FK
conventions, all Lab Knowledge tables use integer primary keys. Where a stable external
identifier is useful, a `uuid` column may be added following the `projects` model
(`backend/app/models/project.py`).

## Rationale

A dedicated section is preferable to overloading the existing `documents` feature because the
two have different lifecycles (experiment-linked vs. operational), different access models, and
different organizational primitives (experiment linkage vs. tags). Building on the existing RBAC,
audit, search, GCS, notification, and async-job systems (rather than new infrastructure) keeps
the feature consistent with the rest of the platform, inherits its compliance properties, and
minimizes new surface area. Integer PKs are chosen over the spec's UUIDs because the audit log
and every existing foreign key in the schema are integer-based; UUID-only tables could not be
referenced by `audit_log.entity_id` without a type mismatch.

## Consequences

**Positive:**

- Labs gain a structured, searchable, auditable institutional memory.
- New hires have a reference for lab-specific terminology.
- Scientific decisions are documented with reasoning, not just outcomes.
- All three components inherit the existing audit log and global search behavior for free.

**Negative:**

- Adds a new top-level navigation section (minor UI complexity).
- Requires users to understand the distinction between Lab Knowledge Documents and the
  experiment-linked Documents feature. This must be communicated clearly in UI copy.

**Risks:**

- Adoption risk: teams may not develop the habit of maintaining SDRs and glossary entries.
  Entry must be as low-friction as possible (AI-assisted glossary population in ADR-062 is
  one mitigation).
