# ADR-009: Immutable Audit Log via Database-Level Enforcement

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)

## Context

bioAF's experiment tracking system requires a complete, tamper-proof audit trail of every action taken on experiments, samples, pipeline runs, and data. This audit trail supports:

- Publication provenance (tracing a figure back to a tissue sample)
- Regulatory compliance (FDA submissions, potential HIPAA/21 CFR Part 11 in v2)
- Internal accountability (who changed what, when)

The audit log must be immutable — no user, including admins, should be able to edit or delete audit entries. This raises the question of where to enforce immutability.

Options:

1. **Application-level enforcement only:** The bioAF API refuses UPDATE/DELETE requests on the audit log table. Easy to implement but a bug, backdoor, or direct database access could bypass it.
2. **Database-level enforcement:** The PostgreSQL role used by the application has INSERT-only permissions on the audit log table. No UPDATE or DELETE grants exist for any role. Enforced by the database engine itself.
3. **Append-only storage (e.g., blockchain, immutable ledger):** Overkill for this use case and adds significant complexity.

## Decision

Immutability is enforced at the PostgreSQL role level. The bioAF application's database user has only INSERT permission on the `audit_log` table. No database role (including the application role and the admin role) has UPDATE or DELETE permissions on this table.

### Schema

```sql
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id INTEGER REFERENCES users(id),
    entity_type VARCHAR(50) NOT NULL,  -- 'experiment', 'sample', 'pipeline_run', etc.
    entity_id INTEGER NOT NULL,
    action VARCHAR(50) NOT NULL,       -- 'create', 'update', 'delete', 'status_change', etc.
    details_json JSONB,                -- what happened (new values, parameters, etc.)
    previous_value_json JSONB          -- what was there before (for updates)
);

-- Role permissions
GRANT INSERT ON audit_log TO bioaf_app;
-- No UPDATE or DELETE grants exist for any role
-- The bioaf_app role also has SELECT for reading the log
GRANT SELECT ON audit_log TO bioaf_app;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO bioaf_app;
```

### What Gets Logged

Every state change on any tracked entity:

- Experiment: create, update metadata, status change, delete (soft)
- Sample: create, update metadata, QC flag change, status change
- Pipeline run: submit, start, complete, fail, cancel
- Notebook session: start, end, files accessed
- Visualization: publish, unpublish
- Data: upload, delete (soft)
- User: create, role change, deactivate
- Infrastructure: component enable/disable, Terraform apply, config change
- Backup: success, failure
- Auth: login, logout, password change, invitation sent/accepted

### Metadata Edit History

When a user updates experiment or sample metadata, the audit log captures both the old and new values:

```json
{
    "action": "update",
    "entity_type": "sample",
    "entity_id": 42,
    "details_json": {
        "field": "treatment_condition",
        "new_value": "10μM Drug X + 5μM Drug Y",
    },
    "previous_value_json": {
        "field": "treatment_condition",
        "old_value": "10μM Drug X"
    }
}
```

## Rationale

- **Database-level enforcement is the strongest guarantee available without specialized infrastructure.** Application code can have bugs. Direct database access (via Cloud SQL proxy or psql) bypasses the application entirely. If the database role itself doesn't have UPDATE/DELETE, the log cannot be tampered with through any normal access path.
- **PostgreSQL role system is well-understood and auditable.** The permission model is simple to verify (`\dp audit_log` in psql shows exactly who can do what).
- **Cloud SQL access is already IAM-controlled.** Connecting to Cloud SQL requires either the Cloud SQL Proxy with IAM auth or direct network access through the VPC. Combined with the role-level INSERT-only restriction, this is defense in depth.
- **Supports future compliance requirements.** 21 CFR Part 11 and HIPAA both require tamper-evident audit trails. Database-level enforcement is a recognized control.

## Consequences

- The bioAF application cannot implement a "purge audit log" feature, even for admins. If storage becomes a concern, old audit entries can be archived to GCS (exported and then the table could theoretically be truncated by a superuser), but this should be a deliberate, documented operational procedure, not a UI feature.
- Schema migrations that alter the `audit_log` table must be handled carefully. Adding columns is fine (ALTER TABLE ... ADD COLUMN). The migration system should never issue UPDATE or DELETE against audit_log.
- The Cloud SQL admin user (used by Terraform for schema migrations) technically has superuser privileges. This is a known gap — in v2, schema migrations could be run by a separate privileged role that is not used at runtime.
- Audit log entries grow indefinitely. For a typical small biotech, this is likely thousands of entries per month — negligible storage. If it becomes a concern, archival to GCS can be implemented without deleting entries (export, then consider partitioning by date).
- The API must ensure every state-changing operation writes to the audit log within the same database transaction as the state change. If the state change succeeds but the audit log write fails, the entire transaction must roll back.

---

## Addendum: API-key actor (2026-05-13)

[ADR-049](ADR-049-service-accounts-and-api-keys.md) introduces a second class of caller (service accounts authenticated by API keys). The audit-log actor tuple is no longer 1:1 with a human user.

### Schema change

- New column: `audit_log.api_key_id BIGINT NULL`. Additive; existing rows have `NULL`. No foreign key constraint so revoking or deleting an `api_keys` row does not cascade into audit history.
- The existing immutability guarantees (no UPDATE, no DELETE; PostgreSQL role lacks those grants on `audit_log`) extend to the new column without any further change.

### Semantics

The actor tuple becomes `(user_id, api_key_id)`:

| `user_id` | `api_key_id` | Caller class |
| --- | --- | --- |
| set | NULL | Human user via JWT (existing behavior) |
| set | set | Service account via API key (new) |
| NULL | NULL | System action (background worker, migration) |

Service-account audit rows always have both columns set: `user_id` is the SA user row, `api_key_id` is the specific key used. This lets operators answer "who did this?" (the SA) and "which credential?" (the key) without joining additional tables.

### Migration

Single Alembic migration adds the column. No backfill required.
