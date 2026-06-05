# ADR-063: Scientific Decision Records (SDR) Status Machine

**Status:** Proposed
**Date:** 2026-06-05
**Deciders:** Brent (product owner)

## Context

SDRs (ADR-059) need a lifecycle reflecting how scientific decisions evolve: they start as
drafts, become active policy, may need re-assessment, and are eventually upheld, replaced, or
repealed. The status machine must enforce valid transitions and keep a complete, immutable
transition history.

Two codebase facts shape the implementation:
- **Org-scoped sequential numbering** already exists: `OrgCodeCounter`
  (`backend/app/models/org_code_counter.py`) plus `CodeService._next_counter(session, org_id, kind)`
  (`backend/app/services/code_service.py`), which allocates a per-org monotonic value under a
  `SELECT ... FOR UPDATE` row lock. The spec's "SELECT MAX(sdr_number)+1 with locking" should not
  be hand-rolled; reuse this with a new `kind="sdr"`.
- **Append-only history with audit** is the established pattern: write a transition row in the
  same transaction as an `audit_service.log_action` call.

## Decision

SDRs follow this status machine, enforced in the **service layer** (not only API validation):

```
draft               -> active
active              -> flagged_for_review   (manual or system-triggered)
active              -> superseded           (requires superseded_by_sdr_id)
active              -> repealed
flagged_for_review  -> active               (requires "decision upheld" note)
flagged_for_review  -> superseded           (requires superseded_by_sdr_id)
flagged_for_review  -> repealed
```

All other transitions are invalid and rejected at the service layer with a 422 at the API.
The transition table is an explicit guard structure (a `dict` of allowed
`{from_status: {to_status: rules}}`) so an invalid transition is impossible to perform through
the service, not merely undocumented.

Every transition writes an append-only `sdr_status_transitions` row (`from_status`, `to_status`,
optional `note`, `transitioned_by_user_id`, `transitioned_at`) and an `audit_log` entry
(`entity_type="sdr"`, `action="status_transitioned"`) in the same transaction. Edits to an
`active` SDR's `decision`/`justification` also write a `sdr_status_transitions` note row recording
the prior values (a record of the edit, not a status change), consistent with the append-only
history approach.

`sdr_number` is allocated at creation via `CodeService._next_counter(session, org_id, "sdr")` and
is immutable. Supersession requires a bidirectional link: marking SDR A `superseded` requires a
valid `superseded_by_sdr_id` (B) in the same org, and sets B's `supersedes_sdr_id = A` if not
already set.

Permissions follow ADR-032: `sdr:view` (all roles), `sdr:author` (create drafts, draft->active;
admin + comp_bio default), `sdr:manage` (all transitions, delete, owner reassign, categories;
admin default). The "view to all roles" requirement is implemented by granting `sdr:view` to all
four system roles, since the RBAC system has no implicit "always on" permission.

## Rationale

A service-layer transition guard makes invalid states unreachable regardless of which endpoint or
caller drives the change, which API-only validation cannot guarantee. Append-only transition rows
plus audit entries give a complete, immutable history consistent with ADR-009. Reusing
`OrgCodeCounter`/`CodeService` for `sdr_number` inherits a concurrency-safe allocator that is
already tested, instead of introducing a second `SELECT MAX+1` implementation with its own locking
edge cases. Bidirectional supersession links let readers trace a decision's evolution in either
direction.

## Consequences

**Positive:**
- Complete, auditable decision history; status reflects the real state of each decision.
- Supersession chains enable tracing a decision's evolution over time.
- Enforcing the "upheld" note on `flagged_for_review -> active` ensures re-assessments are
  documented, not silently dismissed.
- Numbering reuses an existing, concurrency-safe allocator.

**Negative:**
- The status machine is more complex than an active/inactive toggle.
- Supersession requires the superseding SDR to exist before the old one can be marked superseded;
  teams must create the new SDR first.
