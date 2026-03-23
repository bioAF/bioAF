# ADR-032: Custom RBAC with Permission-Based Access Control

**Status:** Accepted
**Date:** 2026-03-22
**Deciders:** Brent (repository owner)

---

## Context

bioAF currently enforces access control through four hardcoded roles: `admin`, `comp_bio`, `bench`, and `viewer`. Each API endpoint checks the user's role string directly via the `require_role()` dependency:

```python
@router.post("/experiments", dependencies=[Depends(require_role("admin", "comp_bio"))])
```

This works for organizations that map cleanly to these four tiers, but real biotech teams have more nuanced structures. A company may employ computational biologists and bioinformaticians with different skill levels and responsibilities. One team may want their senior bioinformatician to manage compute environments while restricting junior members to launching sessions. Another team may want bench scientists to submit pipeline runs but not manage infrastructure.

The hardcoded model cannot express these distinctions. Every new feature (work nodes, environments, image builds) adds more role checks that become increasingly difficult to reconfigure per-organization. The upcoming work node and environment features (ADR-033, ADR-034) introduce several new capabilities that need granular permissions from day one.

---

## Decision

Replace hardcoded role checks with a permission-based RBAC system. Admins define custom roles, assign permissions to those roles, and assign roles to users. The four built-in roles ship with default permission sets that match today's behavior, so existing deployments are unaffected.

### Permission Model

Permissions follow a **resource + action** structure:

| Resource | Actions |
| --- | --- |
| `experiments` | `view`, `create`, `edit`, `delete`, `change_status` |
| `samples` | `view`, `create`, `edit`, `delete` |
| `pipelines` | `view`, `launch`, `cancel`, `configure` |
| `notebooks` | `view`, `launch`, `stop` |
| `work_nodes` | `view`, `launch`, `stop` |
| `environments` | `view`, `create`, `build`, `delete` |
| `files` | `view`, `upload`, `download`, `delete` |
| `projects` | `view`, `create`, `edit`, `delete` |
| `users` | `view`, `invite`, `edit_role`, `deactivate` |
| `infrastructure` | `view`, `configure`, `deploy` |
| `audit_log` | `view` |
| `notifications` | `view`, `configure` |
| `backups` | `view`, `create`, `restore` |
| `cost_center` | `view`, `configure_budgets` |
| `roles` | `view`, `create`, `edit`, `delete` |
| `quotas` | `view`, `configure` |

New resources and actions can be added as features are built without changing the permission engine.

### Data Model

```text
roles
  id, name, description, organization_id, is_system (bool), created_at

role_permissions
  role_id, resource, action
  unique constraint on (role_id, resource, action)

users
  role_id (FK to roles, replaces role string column)
```

System roles (`is_system = true`) are the four built-in roles. They cannot be deleted but their permissions can be customized. Custom roles (`is_system = false`) are created by admins.

### Built-In Role Defaults

| Role | Default Permissions |
| --- | --- |
| `admin` | All permissions on all resources |
| `comp_bio` | Full access to experiments, samples, pipelines, notebooks, work_nodes, environments, files, projects. View-only on users, infrastructure, audit_log, cost_center |
| `bench` | View/create/edit on experiments and samples. View on pipelines, files, projects. Upload files |
| `viewer` | View-only on experiments, samples, pipelines, files, projects |

These defaults are seeded during migration and match the current hardcoded behavior exactly, so existing deployments experience no change in access patterns.

### Endpoint Authorization

The `require_role()` dependency is replaced by `require_permission()`:

```python
# Before
@router.post("/experiments", dependencies=[Depends(require_role("admin", "comp_bio"))])

# After
@router.post("/experiments", dependencies=[Depends(require_permission("experiments", "create"))])
```

The `require_permission()` dependency loads the user's role, checks the `role_permissions` table (cached per-request), and returns 403 if the permission is not granted. Permission lookups are cached in memory with a short TTL (60 seconds) to avoid per-request database queries.

### Admin UI

The role management page renders the resource + action matrix as a grid of checkboxes. Admins can:

- View and customize permissions on built-in roles
- Create new roles by cloning an existing role and adjusting permissions
- Delete custom roles (users on that role must be reassigned first)
- Assign roles to users from the user management page

### Migration Strategy

The migration:

1. Creates the `roles` and `role_permissions` tables
2. Seeds the four built-in roles with their default permission sets
3. Adds a `role_id` FK column to `users`, populates it by matching the existing `role` string to the corresponding built-in role
4. Drops the old `role` string column after backfill
5. Updates all `require_role()` calls to `require_permission()` calls

This is a single migration + codebase-wide refactor. All existing tests are updated to use the new permission model.

---

## Consequences

**Positive:**

- Organizations can define access levels that match their actual team structure
- New features get granular permissions from day one without hardcoding role names
- Built-in roles preserve backwards compatibility; existing deployments work identically
- The resource + action grid is intuitive for admins to configure
- Permission checks are explicit and auditable (the endpoint declares exactly what it needs)

**Negative:**

- Every existing endpoint must be refactored from `require_role()` to `require_permission()` -- this is a large, coordinated change
- Permission caching adds a short window where role changes are not immediately effective (60-second TTL)
- More complex onboarding: admins who previously just picked from four roles now have a permissions grid to understand (mitigated by sensible defaults)
- Role deletion requires reassigning users first, which adds friction

**Neutral:**

- The `is_system` flag on built-in roles prevents accidental deletion but allows permission customization, balancing safety with flexibility
- The permission set will grow as new features are added; the grid UI must scale gracefully (grouping by resource category helps)

---

## References

- ADR-033 (versioned compute environments -- `environments.*` permissions)
- ADR-034 (custom work nodes -- `work_nodes.*` and `quotas.*` permissions)
- ADR-009 (immutable audit log -- role changes are audit-logged)
