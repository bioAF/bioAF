# ADR-070: Organization ownership as an authorization boundary

**Status:** Proposed
**Date:** 2026-11-14
**Deciders:** Brent (repository owner)
**Related:** [ADR-032](ADR-032-custom-rbac.md) (permission model; this ADR adds an ownership requirement alongside
it and replaces none of it), [ADR-049](ADR-049-service-accounts-and-api-keys.md) (API Key scope narrowing; service
accounts are Organization-scoped)

## Context

[ADR-032](ADR-032-custom-rbac.md) answers "may this caller perform this action on this kind of resource?" through
`require_permission(resource, action)`. It says nothing about which records the action may touch. Grants are
Organization-wide (`projects:view` covers every Project in the caller's Organization), so the permission check alone
cannot stop a caller from naming a record ID that belongs to another Organization.

Today every supported install runs one Organization, but an install can already hold several
(`backend/app/cli/create_admin.py` creates an Organization per invocation), and a future multi-organization SaaS
version of bioAF will depend on the boundary. Retrofitting ownership checks after records from several
Organizations share a database is far more expensive and error-prone than enforcing them as each route is written.

Gaps of this kind are not limited to the record named in the URL. A request can carry related IDs (a Project on a
new Experiment, Samples added to a Project, files and Notebook Sessions referenced by an Analysis Snapshot, Samples
named in a Pipeline Run review), and a read can follow stored links to related records. Each of those is a path
across the boundary if only the primary record is checked.

## Decision

Access to an Organization-owned record requires **both** a permission check and an ownership check. A permission
grant is necessary, never sufficient.

1. **Permission.** The route declares its grant with `require_permission`, `require_any_permission` or
   `require_results_view` (`backend/app/api/dependencies.py`), or `require_api_key_permission` on the integration
   API. Secondary or per-record permission decisions inside a handler or helper use `has_effective_permission`, so an
   API Key's scopes narrow the role grant exactly as [ADR-049](ADR-049-service-accounts-and-api-keys.md) requires. A
   role-only `role_service.has_permission` check is not used for caller authorization.
2. **Ownership of the named record.** Every lookup of an Organization-owned record by ID is filtered by the
   caller's Organization (`current_user["org_id"]`) in the query itself. Load through an owned-record helper, such
   as those in `backend/app/api/scoped_loaders.py` (`owned_user_or_404`, `require_owned_experiment`,
   `owned_sample_ids`, `require_owned_sample_batch`), or an equivalent service query that takes the Organization as a
   required argument. Never add an optional Organization argument that defaults to unscoped access.
3. **Ownership of related records.** Every related ID supplied in a request body or query (project, experiment,
   sample, file, template, naming profile, session, run) is checked against the caller's Organization before
   any write. Batches are all-or-nothing: if any ID is foreign or missing, the request fails with no partial writes.
   Reads that follow stored links (detail views, provenance, counts, timelines) filter the related records by
   Organization as well, so a previously stored foreign link discloses nothing.
4. **Records without their own column.** Ownership is resolved through the trusted owning chain, for example a
   Sample through `Sample.experiment_id` to `Experiment.organization_id`. It is never inferred from the requesting
   user, a nullable link, a filename, a tag or a generic output marker.
5. **Foreign looks like missing.** Once the caller's permission requirements are satisfied, a record owned by
   another Organization is indistinguishable from a missing one: both answer **404** with the same message.
   Ownership never produces a 403 that would confirm the record exists. A permission check may answer 403
   (`role_missing`, `key_scope_missing`) before ownership is examined; that response depends only on the caller's
   grants and discloses nothing about whether the requested record exists.
6. **Check before side effects.** Both checks complete before any resource write, cloud or adapter call, remote
   read (for example Google Sheets), outbound delivery, resource audit write or response serialization. Denied
   access causes no resource mutation and no resource-specific external action. Authentication bookkeeping that
   precedes authorization, such as recording an API Key's last use, is outside this guarantee.
7. **Credentials that carry no role.** Content tokens and other short-lived tokens carry the issuing
   Organization and resource type. Authorization happens once at issue and again for direct bearer calls through
   the same helper (`backend/app/api/content_access.py`), and a token resolves only records in its own
   Organization.
8. **Background work.** Work started on behalf of a caller carries the trusted Organization into every later read
   and write (for example, Compute Session status updates are keyed on ID and Organization). Internal scans that
   deliberately span Organizations are system tasks, not caller-authorized paths, and must not be reachable with a
   caller-supplied ID.
9. **Install-wide state is an explicit exception.** Some state is deliberately shared by the install and has no
   Organization owner (for example component state). Such routes rely on a
   server-enforced install-level permission (`infrastructure:*`), and the exception is documented where the route
   is defined. Ownership that is unresolved or ambiguous never becomes implicit cross-organization access.

Single-organization installs see no behavior change for legitimate requests. Cross-Project work inside one
Organization (a Project holding Samples from Experiments in other Projects, an Analysis Snapshot naming an
Experiment outside its Project) remains allowed; the boundary is the Organization, not the Project.

## Rationale

The rule turns authorization into two independent questions (what may this caller do, and which records are
theirs) and makes each one local to the code that loads a record, which is where reviewers and tests can see it.
Filtering in the query rather than checking after the load keeps foreign rows out of memory, out of serializers and
out of logs, and makes "foreign" and "missing" indistinguishable by construction.

Alternatives considered:

- **Permission-only authorization (ADR-032 as written).** Sufficient while every install has one Organization, but it
  leaves every ID route open the moment a second Organization exists, and the boundary would have to be retrofitted
  across hundreds of routes later.
- **Per-record ACLs or Project-level grants.** Finer than needed today; [ADR-049](ADR-049-service-accounts-and-api-keys.md)
  already places per-instance scopes out of scope. The Organization is the tenant unit, and Project grants would
  break legitimate cross-Project work.
- **Database row-level security.** Strong, but it requires session-level Organization context on every connection,
  including background tasks and migrations, and the application already filters explicitly. It remains an option
  once multi-organization SaaS is real.
- **403 for foreign records.** Clearer to a caller but discloses that the record exists in another Organization.
- **Defer until SaaS.** Rejected by the owner: the boundary is cheap to enforce as routes are written and costly to
  add afterwards.

## Consequences

**Positive:** cross-organization reads, writes and external actions are refused at the lookup that would have
reached them; related-record and stored-link paths are covered, not only the URL's primary ID; API Key scopes narrow
every permission decision, including those made inside handlers; the groundwork for multi-organization SaaS is laid
without changing single-organization behavior.

**Negative / honest limits:** this is not full multi-tenancy. Install-wide state, bootstrap paths that use the first
Organization, and ID routes not yet exercised with two Organizations remain prerequisites for SaaS. Orphaned cloud
resources carry no Organization today; whether they are Organization-owned or install-wide is an open owner decision,
and until it is made they are governed by `infrastructure:view` (list) and `infrastructure:deploy` (actions), not by this boundary. Single-organization
fixtures and UI tests cannot demonstrate the boundary, so new routes need two-Organization regressions: a foreign ID
answers 404 with no side effects, and a same-Organization control still succeeds. Every new related-ID field is a new
place the rule must be applied.

**Neutral:** the Organization glossary entry is still pending owner definition; this ADR uses the term as the
top-level owner of Organization-scoped records. The route permission contract
(`backend/tests/test_route_permission_contract.py`) checks that each route declares a grant or a documented
exception; it does not prove ownership, which is covered by behavioral tests.

## References

- [ADR-032](ADR-032-custom-rbac.md) (permission model; extended here with an ownership requirement)
- [ADR-049](ADR-049-service-accounts-and-api-keys.md) (service accounts, API Keys, scope intersection)
- `backend/app/api/dependencies.py`, `backend/app/api/scoped_loaders.py`, `backend/app/api/content_access.py`,
  `backend/app/api/notebook_access.py`
- `backend/tests/test_permissions_and_ownership.py`, `backend/tests/test_route_permission_contract.py`
