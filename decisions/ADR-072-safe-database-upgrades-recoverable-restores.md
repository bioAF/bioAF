# ADR-072: Safe database upgrades and recoverable restores

**Status:** Proposed
**Date:** 2026-11-14
**Deciders:** Brent (repository owner)
**Supersedes:** in [ADR-005](ADR-005-github-based-upgrades.md), the database steps of the CLI upgrade
flow (step 3 "Database is backed up before any changes" and step 7 "Database migrations run via
Alembic"), UI flow step 6 (startup resolves the pending upgrade record), the "Rollback" section, the
"Database backup before update" rationale bullet, and the semantic-versioning bullet in full. Releases
use calendar versions (vYYYY.M.N); a version number carries no compatibility meaning, and every release
must upgrade from every earlier released install (see "Skipped releases stay supported"). In [ADR-004](ADR-004-tiered-backup-strategy.md), the
Tier 1 description of how a dump is staged and trusted ("Dump written to `/tmp/`, uploaded, temp file
deleted"), the "GCS-only storage" constraint as it applies to upgrade recovery backups, and the
statement that a Kubernetes CronJob schedules production backups. ADR-004's four tiers, persistent
backups bucket, mandatory database backups, retention defaults and its ADR-047 addendum stand, as do
ADR-005's GitHub release detection, admin-initiated updates and host-side update agent.

## Context

bioAF is open-source and self-hosted on one VM with Docker Compose. There is no installation
registry or telemetry, so any previously released Alembic install may still exist and may skip any
number of calendar-versioned releases (vYYYY.M.N) when it updates. Compatibility has to be derived
from published release history and each install's own database revision and data.

The earlier upgrade path backed up the database but continued when the backup failed, treated a
running target image as a completed update, and left rollback to a manual restore. The earlier
restore path trusted whatever object it downloaded, tolerated a partial `pg_restore`, and let an
expired review be accepted, which could replace the primary with a stale copy and drop newer
writes. Both paths change the one database that holds the experiment record and audit log
(ADR-004, ADR-009), so every failure must leave that database either unchanged or in a known,
recoverable state, and say what to do next.

## Decision

### One upgrade lifecycle for every entry point

Every schema change goes through `backend/app/schema_upgrade.py`, which the production Alembic
`env.py` calls. The container entrypoint, `./bioaf migrate`, `./bioaf setup`, `./bioaf reset-db`,
`./bioaf update`, the update agent and an older release's own `alembic upgrade head` all reach it.
`python -m app.schema_upgrade run|status` is its command line. `./bioaf reset-db` is an intentional,
destructive reset: it drops and recreates the database before invoking this lifecycle, so only its schema
initialization uses the lifecycle, and none of the preservation guarantees below apply to the reset itself.
Each run:

1. **Serializes** on a PostgreSQL advisory lock; a second runner waits, then finds nothing to do.
2. **Takes a verified recovery backup** before any revision change: a PostgreSQL 16 custom-format
   dump into the durable host `backups/` directory (mounted at `/app/backups`; the image refuses an
   unmounted directory), written as `.partial` and kept only after `pg_dump` exits 0 and the archive's
   table of contents lists data for every table. A manifest records the revision, server and tool
   versions, the dump's SHA-256 and fingerprints of the encryption keys in use. A required free-space
   check runs first. An empty database (fresh install) is not dumped.
3. **Migrates** on the locked connection in one Alembic transaction.
4. **Validates** the migrated database and **records** the outcome in `schema-upgrade.json` in the
   update-status directory.

These steps carry different guarantees:

- **Backup gate.** A failed or unverifiable backup stops the run before any change.
- **Transactional migration.** A migration that fails rolls back with its transaction, leaving the
  schema at its starting revision.
- **Validation gate.** Validation runs after the migration transaction has committed. A validation
  failure does not undo the committed migration; it is recorded as failed, and the compatibility gate
  stays closed so the application does not serve or write against an unvalidated schema. Reversal from
  that state is a restore of the recovery backup, not an automatic rollback.

A failed run is recorded with its step, and the same command retries it, including when the target
image is already installed. The host updater also takes its own gzipped plain-SQL backup, checked
with `gzip -t` and for non-empty content, and stops the update if it fails.

### Completion means the database is upgraded

An update is complete only when the migration succeeded, validation passed and the record says so
for this code's Alembic head. Until then the backend's compatibility gate holds it in maintenance:
API requests get 503, readiness reports unavailable, and no startup writes or background work run.
`UpgradeService.resolve_pending_upgrades` marks an upgrade completed only with that record, and
`./bioaf update` treats a running target image as a no-op only when the database upgrade is settled.

### Skipped releases stay supported

There is no support cutoff. `backend/tests/test_historical_upgrades.py` builds each distinct released
migration tree (from `tests/release_revisions.json`, which picks up new tags automatically) with that
release's own migrations, writes representative data, upgrades it with the candidate's production
environment, and checks data survival, schema parity with a fresh install, no duplicated seeds, and a
no-op second run. Released migrations are frozen (`test_frozen_migrations.py`) and must not import
application code that later changes. A migration that cannot apply to valid historical data is fixed
in the migration with a deterministic, visible transition; source values are not silently rewritten.

### Checksummed backups

Postgres backups in the backups bucket are written through the storage adapter with a marker in
object metadata: format, the backup's own name and the SHA-256 computed at creation
(`app/services/backup_integrity.py`). Creation fails, and the upload is removed, if the marker cannot
be read back. A cutover inventory, `postgres/.integrity-cutover.json`, is written the first time the
new code lists or creates a backup and names the backups that predate markers. Each backup is
classified as checksummed, unverified (named in the inventory) or invalid (anything else, including
an unmarked object made after the cutover). The checksum detects changed bytes under the trusted
backup-writer model; it is not a signature against an actor who can replace both object and metadata.

### Isolated, all-or-nothing restore preparation

`RestoreService.start` admits one restore at a time and prepares it in a private workspace under
`backup_scratch_dir`. Before any `DROP` or `CREATE` of the review database it captures the expected
checksum and identity, enforces a scratch free-space floor before and during the download, bounds the
actual downloaded bytes and the download time, compares the downloaded bytes with the recorded
SHA-256, reads the table of contents, and checks database-volume headroom through the read-only
`pgdata` mount (`restore_db_volume_dir`), which is also watched during the import. It then restores
into a separate `bioaf_restore` database with:

`pg_restore --exit-on-error --single-transaction --no-owner --no-acl` (no parallel jobs)

This is a **transactional import**: any nonzero exit, timeout or limit fails the restore before review,
drops the copy and leaves the primary `bioaf` database untouched. Limits are configured bounds in `app/config.py`
(`restore_max_dump_bytes`, `restore_scratch_floor_*`, `restore_download_timeout_seconds`,
`restore_toc_timeout_seconds`, `restore_pg_restore_timeout_seconds`,
`restore_preparation_timeout_seconds`), overridable per install. API responses and logs carry a
reference, never `pg_dump`/`pg_restore` diagnostics or exception text.

Restoring an unverified (pre-cutover) backup requires an explicit per-request opt-in
(`allow_unverified`) by a user holding `backups:restore`, and is written to the audit log. It is never
labelled checksum-verified.

### Explicit acceptance or reversal

A prepared restore enters a review window (`RESTORE_REVIEW_SECONDS`). Automatic background work and
the Pub/Sub listener are paused, and traffic is served from `bioaf_restore`. The admin then calls
`POST /api/backups/restore/accept` or `/restore/reject`. Accept is refused before any swap or SQL once
the review has expired, is already being decided, or needs recovery. Reject, or expiry, returns traffic
to the original database, drops the copy and resumes work.

Accepting is a **recoverable cutover**, not a single transaction: the primary is renamed aside, the
review copy is renamed into place, and the old primary is dropped, as separate PostgreSQL operations.
If Accept fails, the service classifies the resulting state before doing anything else:

- **Untouched** (nothing renamed): the review stays open to accept, until it expires, or reject.
- **Replaced** (the copy is now the primary): the service retries the reconnection and completes the
  Accept.
- **Anything else** (a partial rename): the service enters a recovery-required state, blocks new
  restores and automatic work, and tells the operator how to recover. Automatic work resumes only
  against a verified primary.

Any failure that cannot be cleaned up safely likewise keeps `bioaf_restore` and requires recovery.

### Encryption keys stay out of backups

Neither recovery backups nor bucket backups contain `BIOAF_ENCRYPTION_KEYS` (ADR-047: the backup is
the threat). The manifest records key fingerprints, and recovery documentation names `docker/.env`
as part of the matched recovery set, kept in a separate trust boundary.

## Rationale

- **Warn and continue on backup failure** was rejected: an upgrade without recovery material is not
  safe to proceed, and stopping before mutation is always retryable.
- **A support cutoff or fleet census** was rejected: a self-hosted open-source project cannot know
  which releases are in the field, so release history is the only reliable source.
- **Image-installed as completion** was rejected: it reported success while the schema was behind
  and let new code run against it.
- **Restoring directly over the primary** was rejected: a bad dump would destroy the only good copy.
  A separate review database keeps the primary intact until an explicit decision.
- **Tolerating nonzero `pg_restore` exits or a stderr allowlist** was rejected: a partial restore that
  looks successful is worse than a refusal. A single transaction makes the import all or nothing.
- **Computing a checksum at restore time** was rejected: it proves nothing about what was written.
  Only a creation-time digest, recorded with the object, is a meaningful comparison.
- **Refusing all legacy backups** was rejected: it would strand existing recovery material, so they
  stay usable through an audited, explicit opt-in.
- **Copying encryption keys beside dumps** was rejected: it would undo at-rest encryption.

## Consequences

**Positive:** every entry point shares one lifecycle; a failed backup or migration leaves the schema as
it was and is retryable; a failed validation cannot let the application run against the unvalidated
schema; any released install can update straight to the current release; a restore import either fully
succeeds into an isolated copy or changes nothing; a late Accept cannot overwrite newer writes; a
cutover failure is classified and either completed, left reviewable or surfaced for recovery.

**Negative / honest limits:**

- Upgrades need free disk for the recovery dump (`1.5 x pg_database_size` plus a reserve) and fail
  rather than proceed without it. Routine verification reads the archive's table of contents; full
  restores of recovery dumps are exercised in tests, not on every upgrade.
- Restores need roughly twice the larger of the primary and the dump in database-volume headroom.
  Development Compose does not mount the volume, so headroom there is reported as not measured.
- The restore limits are provisional configured bounds, not measured guarantees; they bound the dump
  and preparation time, not database or WAL growth.
- A validation failure leaves a committed migration in place behind a closed gate; returning to the
  previous schema requires restoring the recovery backup.
- Accept's cutover spans several database operations; a failure partway can require operator
  recovery.
- Accept drops the previous primary; nothing after Accept restores it except another backup.
- Restores run without parallel jobs, so large databases restore more slowly.
- Recovering any backup also needs the matching encryption keys. Losing the required keys makes
  encrypted columns unreadable; losing `docker/.env` is recoverable if those keys are preserved elsewhere.

## References

- [ADR-004](ADR-004-tiered-backup-strategy.md), [ADR-005](ADR-005-github-based-upgrades.md),
  [ADR-009](ADR-009-immutable-audit-log.md), [ADR-047](ADR-047-data-at-rest-encryption.md)
- `backend/app/schema_upgrade.py`, `backend/app/services/backup_service.py`,
  `backend/app/services/backup_integrity.py`, `backend/app/services/backup_resources.py`,
  `backend/app/api/backups.py`, `backend/app/middleware/maintenance.py`, `./bioaf`
- `backend/tests/test_schema_upgrade.py`, `test_historical_upgrades.py`, `test_frozen_migrations.py`,
  `test_restore_preflight.py`, `test_restore_expiry.py`, `test_backup_integrity.py`
