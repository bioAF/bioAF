#!/usr/bin/env bash
# The first update from a released install, performed by that release's own ./bioaf (plan 9).
#
# An older ./bioaf runs its in-memory cmd_update: it backs up (warning and carrying on if that
# fails), checks out the new tag, starts the new containers, and finally runs
# `alembic upgrade head` in the new backend. None of the new host-side code runs. This proves the
# candidate still delivers the lock, the verified backup and the gate through what that update does
# read fresh: the new image, its entrypoint and docker-compose.yml.
#
# Two scenarios, each on a fresh install of OLD_TAG with data:
#   1. a normal first update: the database reaches the candidate head, a verified recovery dump is
#      on the host in backups/, the backend serves normally, the data is intact;
#   2. the candidate's backup cannot be written: the database is not changed, the backend serves
#      503 maintenance with the reason, and once the cause is fixed the NEW ./bioaf retries with the
#      target image already installed and completes.
#
# Needs Docker, the OLD_TAG images (ghcr.io/bioaf/bioaf-{backend,frontend}:OLD_TAG) and a candidate
# backend image built from this checkout:
#   docker build --platform linux/amd64 -f docker/Dockerfile.backend -t bioaf-candidate-backend:plan9 backend
#   tests/integration/first_update_from_release.sh v2026.11.7 bioaf-candidate-backend:plan9
# Uses host ports 80 and 443 (the release's nginx). Leaves nothing running.
set -euo pipefail

OLD_TAG="${1:-v2026.11.7}"
CANDIDATE_IMAGE="${2:-bioaf-candidate-backend:plan9}"
CANDIDATE_VERSION="9999.0.0"
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
CANDIDATE_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
WORK="$(mktemp -d)"
export DOCKER_DEFAULT_PLATFORM=linux/amd64

say() { printf '\n=== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

docker tag "$CANDIDATE_IMAGE" "ghcr.io/bioaf/bioaf-backend:v$CANDIDATE_VERSION"
docker tag "ghcr.io/bioaf/bioaf-frontend:$OLD_TAG" "ghcr.io/bioaf/bioaf-frontend:v$CANDIDATE_VERSION"

install_release() {
    # install_release <dir> <project>: a running OLD_TAG install with data, at its Alembic head.
    local dir="$1" project="$2"
    git clone -q "$REPO" "$dir"
    git -C "$dir" checkout -q "$OLD_TAG"
    # The candidate, tagged as a release the old updater can fetch and check out (local clone only).
    git -C "$dir" tag "v$CANDIDATE_VERSION" "$CANDIDATE_COMMIT"
    git -C "$REPO" tag -f "v$CANDIDATE_VERSION" "$CANDIDATE_COMMIT" >/dev/null
    cat > "$dir/docker/.env" <<ENV
COMPOSE_PROJECT_NAME=$project
POSTGRES_PASSWORD=plan9pw
DATABASE_URL=postgresql+asyncpg://bioaf:plan9pw@db:5432/bioaf
SECRET_KEY=plan9-first-update-check-0123456789abcdef0123456789
BIOAF_ENCRYPTION_KEYS=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
BIOAF_IMAGE_TAG=$OLD_TAG
ENV
    (cd "$dir" && ./bioaf start >/dev/null && ./bioaf migrate >/dev/null)
    docker compose -f "$dir/docker/docker-compose.yml" --env-file "$dir/docker/.env" exec -T db psql -U bioaf -q -c "
        INSERT INTO organizations (name) VALUES ('Lab');
        INSERT INTO projects (organization_id, name, status) VALUES (1, 'P', 'active');
        INSERT INTO experiments (organization_id, project_id, name, status) VALUES (1, 1, 'E', 'registered');
        INSERT INTO samples (experiment_id, external_id, status)
            SELECT 1, 'S-' || g, 'registered' FROM generate_series(1, 25) g;
        UPDATE platform_config SET value = 'lab-project' WHERE key = 'gcp_project_id';"
}

old_update() {
    # The released ./bioaf's own cmd_update, with only its network lookups stubbed. The images are
    # already local, so its pull step runs PULL_STEP (default: nothing) instead.
    local dir="$1" pull_step="${2:-true}"
    (cd "$dir" && BIOAF_AUTO_CONFIRM=1 BIOAF_SKIP_RESTART_WARN=1 PULL_STEP="$pull_step" bash -c '
        source ./bioaf >/dev/null
        verify_release_images() { return 0; }
        ensure_cloud_logging() { :; }
        DC_PULL="$PULL_STEP"
        cmd_update '"$CANDIDATE_VERSION"' --yes')
}

dc() { local dir="$1"; shift; docker compose -f "$dir/docker/docker-compose.yml" --env-file "$dir/docker/.env" "$@"; }
sql() { dc "$1" exec -T db psql -U bioaf -At -c "$2"; }
ready_status() {
    dc "$1" exec -T backend python -c "
import urllib.request, urllib.error
try:
    print(urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)" 2>/dev/null || echo down
}
wait_ready_status() {
    local dir="$1" want="$2" got=""
    for _ in $(seq 1 60); do
        got="$(ready_status "$dir")"
        [ "$got" = "$want" ] && return 0
        sleep 3
    done
    fail "readiness was $got, expected $want"
}
teardown() { for d in "$WORK"/*/; do [ -f "$d/docker/.env" ] && dc "$d" down -v >/dev/null 2>&1 || true; done; rm -rf "$WORK"; git -C "$REPO" tag -d "v$CANDIDATE_VERSION" >/dev/null 2>&1 || true; }
trap teardown EXIT

say "Scenario 1: first update by $OLD_TAG's own updater"
A="$WORK/a"; install_release "$A" plan9a
before="$(sql "$A" "SELECT count(*) FROM samples")"
old_head="$(sql "$A" "SELECT version_num FROM alembic_version")"
old_update "$A" || fail "the release's update exited nonzero"
[ "$(sql "$A" "SELECT version_num FROM alembic_version")" != "$old_head" ] || fail "database not upgraded"
[ "$(sql "$A" "SELECT count(*) FROM samples")" = "$before" ] || fail "samples changed"
[ "$(sql "$A" "SELECT value FROM platform_config WHERE key = 'gcp_project_id'")" = "lab-project" ] || fail "setting lost"
ls "$A"/backups/bioaf_pre_upgrade_"${old_head}"_to_*.dump >/dev/null || fail "no recovery dump on the host"
ls "$A"/backups/bioaf_pre_upgrade_"${old_head}"_to_*.json >/dev/null || fail "no dump manifest"
grep -q '"pg_dump": "pg_dump (PostgreSQL) 16' "$A"/backups/bioaf_pre_upgrade_*.json || fail "dump not made by PostgreSQL 16"
dc "$A" exec -T backend python -m app.schema_upgrade status | grep -q '"settled": true' || fail "upgrade not recorded"
wait_ready_status "$A" 200
grep -q '"status": "completed"' "$A/update-status/current.json" || fail "old updater did not record completion"
echo "PASS: scenario 1 ($old_head -> $(sql "$A" "SELECT version_num FROM alembic_version"), $before samples kept, dump on host)"

say "Scenario 2: the candidate cannot write its recovery backup at the first update"
B="$WORK/b"; install_release "$B" plan9b
old_head="$(sql "$B" "SELECT version_num FROM alembic_version")"
# After the old updater's own backup (it would stop at its mkdir otherwise) and before it starts the
# new containers, backups/ becomes a file: Compose mounts it at /app/backups, where no dump can go.
printf '%s\n' '#!/bin/sh' "mv '$B/backups' '$B/backups.host' && touch '$B/backups'" > "$WORK/break-backups"
chmod +x "$WORK/break-backups"
if old_update "$B" "$WORK/break-backups"; then fail "the update reported success without a backup"; fi
[ "$(sql "$B" "SELECT version_num FROM alembic_version")" = "$old_head" ] || fail "database changed without a backup"
wait_ready_status "$B" 503
dc "$B" exec -T backend python -m app.schema_upgrade status | grep -q '"step": "backup"' || fail "failure not recorded"
echo "PASS: scenario 2a (no backup -> nothing changed, backend in maintenance)"

rm -f "$B/backups"; mv "$B/backups.host" "$B/backups"
dc "$B" up -d backend >/dev/null  # remount the fixed directory
(cd "$B" && BIOAF_AUTO_CONFIRM=1 ./bioaf update "$CANDIDATE_VERSION" --yes) || fail "retry with the image installed failed"
[ "$(sql "$B" "SELECT version_num FROM alembic_version")" != "$old_head" ] || fail "retry did not upgrade"
wait_ready_status "$B" 200
echo "PASS: scenario 2b (retry with the target image installed completed)"
