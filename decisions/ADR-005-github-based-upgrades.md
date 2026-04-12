# ADR-005: GitHub-Based Versioning and Upgrade System

**Status:** Accepted
**Date:** 2026-03-05
**Deciders:** Brent (product owner)
**Updated:** 2026-04-12 (aligned with implementation)

## Context

bioAF is a self-hosted platform deployed into customer GCP projects. Unlike a SaaS product, updates cannot be pushed centrally. Customers need a reliable, transparent mechanism to discover and apply updates to their bioAF instances.

Options considered:

1. **Container registry polling:** bioAF checks a container registry for new tags. Simple but doesn't communicate what changed.
2. **GitHub releases:** bioAF checks the GitHub repo for new release tags. Provides changelogs, release notes, and community transparency.
3. **Apt/yum-style package repository:** Over-engineered for this use case.
4. **Auto-update (pull and apply automatically):** Dangerous for infrastructure-managing software. A bad auto-update could destroy a customer's compute environment.

## Decision

bioAF versions are published as tagged releases on the bioAF GitHub repository. The bioAF backend queries the GitHub Releases API to detect available updates. All upgrades are admin-initiated and require explicit confirmation.

### Release Artifacts

Each GitHub release includes:

- A git tag (e.g., `v0.7.0`) on the repository
- Database migration scripts (if schema changes are needed)
- Changelog in the release body

### Update Detection

- The backend queries the GitHub Releases API on startup and on a daily schedule
- Compares the current running version (from `pyproject.toml`) against the latest release tag
- When a newer version exists, displays a banner on the Settings > Information page
- Users subscribed to platform events receive notifications via in-app, email, or Slack

### Upgrade Flow (CLI)

1. Admin runs `./bioaf update` (latest) or `./bioaf update <version>`
2. The script validates the version and confirms with the admin
3. Database is backed up before any changes
4. The target version tag is fetched and checked out
5. Container images are rebuilt locally via `docker compose build`
6. Services are restarted via `docker compose up -d`
7. Database migrations run via Alembic
8. Status is written to `update-status/current.json` throughout the process

### Upgrade Flow (UI)

1. Admin reviews changelog on the Settings > Information page
2. Admin clicks "Install Update"
3. The backend writes a trigger file to `update-requests/`
4. A host-side update agent (systemd service) picks up the trigger and runs `./bioaf update <version>`
5. The frontend polls `GET /api/upgrades/status` for progress
6. After services restart, the backend resolves the pending upgrade record on startup
7. Upgrade event is recorded in upgrade history

### Rollback

Rollback is manual. If an update fails or causes issues:

- The database backup created before the update can be restored
- `git checkout v<previous-version>` followed by `./bioaf update <previous-version>` reverts the code
- Upgrade history tracks all attempts with their status (completed, failed, rolled_back)

## Rationale

- **GitHub is where the project lives.** bioAF is open-source. Users already interact with the GitHub repo for issues, discussions, and contributions. Using GitHub releases as the version source is natural.
- **Git tags over container registries.** The Docker Compose deployment rebuilds images locally, so there is no need for a separate container registry. Git tags are the single source of truth for versions.
- **Explicit admin confirmation prevents accidents.** Infrastructure-managing software should never auto-update.
- **Database backup before update.** Every update creates a pre-update database backup, providing a safety net if migrations fail.
- **Host-side update agent.** The backend runs inside a Docker container and cannot restart itself. A lightweight systemd service on the host watches for trigger files and executes the update, bridging the container-to-host gap.
- **Semantic versioning** (MAJOR.MINOR.PATCH) with clear policies: MAJOR = breaking changes, MINOR = new features, PATCH = bug fixes and security patches.

## Consequences

- The control plane needs network egress to api.github.com for version checks and to github.com for `git fetch`. Both are available via Cloud NAT on GCE.
- The update agent systemd service is installed during `./bioaf setup` and must be running for UI-triggered updates to work.
- Air-gapped deployments would need a manual upgrade path: download the repo archive, extract it, and run `./bioaf update <version>`. This is a future concern.
