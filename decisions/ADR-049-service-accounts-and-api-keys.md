# ADR-049: Service Accounts and API Key Authentication

**Status:** Accepted
**Date:** 2026-05-13
**Deciders:** Brent (repository owner)

---

## Context

The public integration API ([ADR-048](ADR-048-public-integration-api-surface.md)) needs an identity that survives any one human user leaving the organization, carries an explicit permission envelope, and never participates in the interactive login flow. The existing identity model is human users only: email + password ([ADR-003](ADR-003-email-based-auth.md)) issuing JWTs, optionally augmented with session credentials for RStudio PAM auth ([ADR-030](ADR-030-session-credentials-pam-auth.md)).

A LIMS integrator needs:

- A non-human caller scoped to a single organization.
- Multiple credentials per caller for rotation.
- A permission envelope tighter than the caller's role (so a "key for sample sync" cannot read finance fields even if the role technically grants it).
- A revocation path that does not delete the caller.
- An audit trail that records both the caller and the specific credential used.

---

## Decision

Service accounts are `User` rows with `is_service_account=true`. API keys are separate rows in a new `api_keys` table, each linked to a service-account user. Effective permission for a request is the strict intersection of the service account's role permissions and the key's per-call scope list.

### Key components

- **Identity model:** `users.is_service_account` boolean (additive). SAs have a synthetic non-routable email (`sa-{slug}-{rand}@org{N}.bioaf.svc`; `.svc` avoids the reserved-TLD list that pydantic `EmailStr` rejects), a display name shown in the admin UI, an organization, and a role. They have no password hash.
- **Login lockout:** every interactive auth path (`auth_service.login`, refresh, password reset) rejects users with `is_service_account=true`. SAs never get a JWT.
- **Credentials:** `api_keys` table. Multiple keys per SA. Each key has a prefix, a bcrypt hash, a JSON scope list, created_by, last_used_at, and revoked_at.
- **Key format:** `biokey_<12-char-prefix>.<32-char-secret>`. The prefix is indexed for cheap lookup; the bcrypt comparison runs only on the matched row.
- **Scope alphabet:** `resource:action` strings drawn from the existing permission registry ([ADR-032](ADR-032-custom-rbac.md)). Validated at mint-time. v1 subset: `projects:{view,create,edit}`, `experiments:{view,create,edit}`, `samples:{view,create,edit}`, `files:view`. No `*:delete`, no status writes.
- **Authorization rule:** the integration auth path sets `request.state.current_user["scopes"]` and `["api_key_id"]`. `require_permission(resource, action)` performs its existing role check, then -- if `api_key_id` is set -- additionally checks `f"{resource}:{action}" in current_user["scopes"]`. JWT requests have `api_key_id=None` and skip the scope check.
- **Rotation:** mint a new key, leave the old one active during cutover, revoke the old one. No forced rotation cadence in v1.
- **Audit:** `audit_log.api_key_id` is added as a nullable column. Every integration-route handler writes an audit row with `user_id=sa_user_id` and `api_key_id=key_id`. The addendum to [ADR-009](ADR-009-immutable-audit-log.md) covers the actor tuple change.

### Why store the bcrypt hash of the full `<prefix>.<secret>` string

The prefix is unique enough on its own to identify a row, but hashing the full presented string means a leaked database row cannot be replayed against a partial leak of just the secret half. The prefix index is for lookup speed; the hash compare is the real check.

### Why a separate `api_keys` table instead of reusing `password_hash` on the SA user

- Multiple credentials per SA. A user row holds a single password hash.
- Per-key scopes. Scopes belong to the credential, not the identity.
- Revocation without deleting the SA. `revoked_at` per key.
- Cleaner audit story. `audit_log.api_key_id` is meaningful only as a foreign-key-like pointer; a single hash column on `users` would not carry that.

---

## Out of scope

- Per-resource-instance ACLs (key scoped to a single project). Scopes are `resource:action` only in v1.
- Forced rotation. Admins decide rotation cadence.
- Key creation outside the admin UI. No `bioaf keys mint` CLI in v1.
- Cross-org service accounts. SAs are strictly org-scoped.
- mTLS or IP allowlists.

---

## Consequences

### Positive

- Sets the pattern for any future machine-to-machine identity (CI tokens, scheduled jobs, internal service-to-service) without further model work.
- Role + scope intersection is a familiar mental model for integrators and matches how Stripe restricted keys, GCP service accounts, and GitHub fine-grained tokens behave.
- Auditing tells you not just "the SA did X" but "key K of the SA did X", which is the right granularity for revocation decisions during an incident.

### Negative

- Two layers of authorization (role check + scope check) means a 403 can come from either layer. The error body distinguishes (`detail="role_missing"` vs `"key_scope_missing"`).
- The synthetic email scheme is ugly. The display_name field exists so the UI never has to show it.
- `last_used_at` on a hot key would write on every request without debouncing. The middleware debounces to once per minute per key.

---

## References

- [ADR-003](ADR-003-email-based-auth.md) -- existing human auth path; service accounts cannot use it.
- [ADR-009](ADR-009-immutable-audit-log.md) -- actor tuple addendum.
- [ADR-030](ADR-030-session-credentials-pam-auth.md) -- prior precedent for non-JWT credentials, but for a very different use case.
- [ADR-032](ADR-032-custom-rbac.md) -- scope intersection addendum.
- [ADR-048](ADR-048-public-integration-api-surface.md) -- the surface this identity authenticates against.
- Spec: `documentation/spec-lims-integration-auth.md`
