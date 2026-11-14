#!/usr/bin/env bash
# Deployment generations and the install-wide deployment lock (issue #110, stages A and B).
#
# Sourced by ./bioaf. Every operation that changes what runs (update, build, setup) takes the lock first, so
# two of them never overlap, and records generations in host-state/deployments.json:
#
#   current    the backend and frontend images running now, by immutable image ID, with the tag they ran under
#   previous   the generation before it (the rollback images)
#   candidate  an operation that started and has not yet run its images (update, build or setup, and its tag);
#              still present at the next operation means that one was interrupted or failed
#
# A tag is not identity: a same-tag rebuild or a one-service build is a new generation. The record is written
# atomically (a reader never sees half a file) and privately (0700 directory, 0600 file). A damaged record is
# reported and left as it is, never replaced as if empty. Nothing here deletes images: the retention budgets
# that would are a separate, owner-approved stage.
#
# Needs: SCRIPT_DIR, DC (docker compose), python3. DEPLOY_DOCKER overrides the docker CLI (tests).

# ./bioaf defines these; standing alone (tests, the update agent) they print plainly.
declare -F red >/dev/null || red() { printf '%s\n' "$*" >&2; }
declare -F yellow >/dev/null || yellow() { printf '%s\n' "$*" >&2; }

DEPLOY_BUSY=75
DEPLOY_LOCK_WAIT="${BIOAF_DEPLOY_LOCK_WAIT:-0}"   # seconds to wait for another operation; 0 refuses at once
DEPLOY_SERVICES="backend frontend"

deploy_state_dir() { echo "$SCRIPT_DIR/host-state"; }

_deploy_ensure_dir() {
    local dir
    dir="$(deploy_state_dir)"
    [ -d "$dir" ] || mkdir -m 700 "$dir" || return 1
    chmod 700 "$dir"
}

deploy_lock() {
    # Take the install-wide deployment lock on fd 9 (host-state/deployment.lock). It belongs to the open file, so
    # it is held until fd 9 closes: when the calling shell exits, however it exits. Returns DEPLOY_BUSY when
    # another operation holds it past DEPLOY_LOCK_WAIT. Python takes it because macOS has no flock(1).
    _deploy_ensure_dir || return 1
    exec 9>>"$(deploy_state_dir)/deployment.lock" || return 1
    python3 -I -c '
import fcntl, sys, time
deadline = time.monotonic() + float(sys.argv[1])
while True:
    try:
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except BlockingIOError:
        if time.monotonic() >= deadline:
            sys.exit(int(sys.argv[2]))
        time.sleep(0.2)
' "$DEPLOY_LOCK_WAIT" "$DEPLOY_BUSY"
}

deploy_running_ids() {
    # "<backend image id> <frontend image id>" of the running containers, or nonzero if either is not running.
    local svc cid id ids=()
    for svc in $DEPLOY_SERVICES; do
        cid="$($DC ps -q "$svc" 2>/dev/null | head -n 1)"
        [ -n "$cid" ] || return 1
        id="$("${DEPLOY_DOCKER:-docker}" inspect --format '{{.Image}}' "$cid" 2>/dev/null)" || return 1
        [ -n "$id" ] || return 1
        ids+=("$id")
    done
    echo "${ids[*]}"
}

_deploy_update() {
    # Apply one change to the record with Python (json, atomic replace). $1 is the change; the rest are its args.
    _deploy_ensure_dir || return 1
    python3 -I - "$(deploy_state_dir)/deployments.json" "$@" <<'PY'
import datetime, json, os, sys, tempfile

path, change, *args = sys.argv[1:]
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
try:
    with open(path) as f:
        record = json.load(f)
except FileNotFoundError:
    record = {"format": 1, "current": None, "previous": None, "candidate": None}
except ValueError:
    print(f"The deployment record {path} is damaged; it was left unchanged.", file=sys.stderr)
    sys.exit(2)
if record.get("format") != 1:
    print(f"The deployment record {path} has an unknown format; it was left unchanged.", file=sys.stderr)
    sys.exit(2)

if change == "candidate":
    operation, tag = args
    record["candidate"] = {"operation": operation, "tag": tag, "started_at": now}
elif change == "running":
    tag, backend, frontend = args
    current = record.get("current")
    if current and current.get("backend") == backend and current.get("frontend") == frontend:
        if record.get("candidate") is None:
            sys.exit(0)  # nothing changed: the file is not rewritten
    else:
        record["previous"] = current
        record["current"] = {"tag": tag, "backend": backend, "frontend": frontend, "recorded_at": now}
    record["candidate"] = None
else:
    sys.exit(f"unknown change {change}")

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".deployments-")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(record, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
except BaseException:
    os.unlink(tmp)
    raise
PY
}

deploy_mark_candidate() {
    # Record that <operation> toward <tag> started; it stays recorded until deploy_record_running sees it run.
    _deploy_update candidate "$1" "$2"
}

deploy_record_running() {
    # Record the running backend and frontend images, under <tag>, as the current generation (the old current
    # becomes previous; the same pair changes nothing). Fails, recording nothing, if a service is not running.
    local tag="$1" ids
    ids="$(deploy_running_ids)" || return 1
    # shellcheck disable=SC2086  # two words: the backend and frontend image IDs
    _deploy_update running "$tag" $ids
}

deploy_interrupted() {
    # Prints the operation an earlier run left unfinished ("update to v1.2.3 started <time>"); nonzero if none.
    local path
    path="$(deploy_state_dir)/deployments.json"
    [ -f "$path" ] || return 1
    python3 -I - "$path" <<'PY'
import json, sys
try:
    c = json.load(open(sys.argv[1])).get("candidate")
except ValueError:
    sys.exit(1)
if not c:
    sys.exit(1)
print(f"{c['operation']} to {c['tag']} started {c['started_at']}")
PY
}

# --- stage C: retention (owner-approved budgets, issue #110) ---------------------------------------------------
#
# Run before staging a new generation. Only bioAF's own artifacts are touched: images carrying the label its
# Dockerfiles set, updater-made dumps by name, the agent's own log. Unknown ownership, a damaged record, or an
# image any container uses means the artifact is left in place and the reason reported.

DEPLOY_IMAGE_LABEL="org.opencontainers.image.source=https://github.com/bioAF/bioAF"
DEPLOY_IMAGE_REPO_PREFIX="ghcr.io/bioaf/bioaf-"   # docker/docker-compose.yml image names
DEPLOY_BUILD_CACHE_BYTES="${BIOAF_BUILD_CACHE_BYTES:-$((5 * 1024 * 1024 * 1024))}"
DEPLOY_BUILD_CACHE_UNUSED="${BIOAF_BUILD_CACHE_UNUSED:-168h}"
DEPLOY_NEW_IMAGES_BYTES="${BIOAF_NEW_IMAGES_BYTES:-$((7 * 512 * 1024 * 1024))}"   # 3.5 GiB
DEPLOY_FLOOR_MIN_BYTES="${BIOAF_DISK_FLOOR_MIN_BYTES:-$((3 * 1024 * 1024 * 1024))}"
DEPLOY_FLOOR_PERCENT="${BIOAF_DISK_FLOOR_PERCENT:-10}"
DEPLOY_KEEP_DUMP_SETS="${BIOAF_KEEP_UPDATE_DUMPS:-2}"

_deploy_docker() { "${DEPLOY_DOCKER:-docker}" "$@"; }

_deploy_kept_ids() {
    # Image IDs that must stay: every container's image (running or stopped), and the current, previous and
    # candidate generations in the record. Fails (keeping everything) when the record cannot be read.
    local path cid
    path="$(deploy_state_dir)/deployments.json"
    for cid in $(_deploy_docker ps -aq 2>/dev/null); do
        _deploy_docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true
    done
    [ -f "$path" ] || return 0
    local recorded tag svc
    recorded="$(python3 -I - "$path" <<'PY'
import json, sys
try:
    record = json.load(open(sys.argv[1]))
except ValueError:
    sys.exit(1)
for generation in ("current", "previous", "candidate"):
    g = record.get(generation) or {}
    for service in ("backend", "frontend"):
        if g.get(service):
            print(g[service])
print("tag", (record.get("candidate") or {}).get("tag") or "")
PY
)" || return 1
    grep -v '^tag ' <<< "$recorded" || true
    # Images already carrying the candidate's tag (a retried update, a pre-pulled release) are its own.
    tag="$(sed -n 's/^tag //p' <<< "$recorded")"
    if [ -n "$tag" ]; then
        for svc in $DEPLOY_SERVICES; do
            _deploy_docker inspect --type image --format '{{.Id}}' "${DEPLOY_IMAGE_REPO_PREFIX}${svc}:${tag}" 2>/dev/null || true
        done
    fi
}

deploy_prune_images() {
    # Remove bioAF images no container and no recorded generation uses (docker rmi without force: Docker itself
    # refuses one still in use). Removing a tag does not always free bytes: layers shared with kept images stay.
    local kept id removed=0
    if [ ! -f "$(deploy_state_dir)/deployments.json" ]; then
        yellow "No deployment generations are recorded yet, so old bioAF images were left in place."
        return 0
    fi
    if ! kept="$(_deploy_kept_ids)"; then
        yellow "The deployment record could not be read, so old bioAF images were left in place."
        return 0
    fi
    for id in $(_deploy_docker images -q --no-trunc --filter "label=$DEPLOY_IMAGE_LABEL" 2>/dev/null | sort -u); do
        if ! grep -qxF "$id" <<< "$kept"; then
            if _deploy_docker rmi "$id" >/dev/null 2>&1; then
                removed=$((removed + 1))
            else
                yellow "An old bioAF image could not be removed (${id:7:12}); it was left in place."
            fi
        fi
    done
    echo "Removed $removed old bioAF image(s)."
}

deploy_prune_build_cache() {
    # Keep the build cache within its budget, removing only cache unused for DEPLOY_BUILD_CACHE_UNUSED.
    local flag=--keep-storage
    _deploy_docker builder prune --help 2>/dev/null | grep -q -- '--max-used-space' && flag=--max-used-space
    _deploy_docker builder prune -f "$flag" "$DEPLOY_BUILD_CACHE_BYTES" --filter "until=$DEPLOY_BUILD_CACHE_UNUSED" >/dev/null \
        || yellow "The Docker build cache could not be pruned; it was left as it is."
}

_deploy_disk_usage() {
    # "<total bytes> <free bytes>" of the filesystem holding Docker's data (images, cache, volumes).
    local root
    root="$(_deploy_docker info --format '{{.DockerRootDir}}' 2>/dev/null)" || root=/
    [ -d "$root" ] || root=/
    python3 -I -c 'import shutil, sys; u = shutil.disk_usage(sys.argv[1]); print(u.total, u.free)' "$root"
}

deploy_require_space() {
    # deploy_require_space <database bytes>: refuse unless the new images and a pre-update backup (1.5x the
    # database) leave free space above max(DEPLOY_FLOOR_MIN_BYTES, DEPLOY_FLOOR_PERCENT% of the disk).
    local db="${1:-0}" usage total free needed floor
    usage="$(_deploy_disk_usage)" || { red "ERROR: Free disk space could not be measured."; return 1; }
    read -r total free <<< "$usage"
    needed=$((DEPLOY_NEW_IMAGES_BYTES + db * 3 / 2))
    floor=$((total * DEPLOY_FLOOR_PERCENT / 100))
    [ "$floor" -ge "$DEPLOY_FLOOR_MIN_BYTES" ] || floor="$DEPLOY_FLOOR_MIN_BYTES"
    if [ $((free - needed)) -lt "$floor" ]; then
        red "ERROR: Not enough free disk space: $((free / 1048576)) MiB free, $((needed / 1048576)) MiB needed for the new images and a backup, and $((floor / 1048576)) MiB must stay free."
        return 1
    fi
}

deploy_rotate_dumps() {
    # Keep the DEPLOY_KEEP_DUMP_SETS newest dumps of each kind the updater makes (bioaf_pre_update_*.sql.gz from
    # ./bioaf update, bioaf_pre_upgrade_*.dump with its .json manifest from the database upgrade). Manual
    # backups, other names and links are never touched.
    local dir="$SCRIPT_DIR/backups" pattern
    [ -d "$dir" ] || return 0
    for pattern in 'bioaf_pre_update_*.sql.gz' 'bioaf_pre_upgrade_*.dump'; do
        python3 -I - "$dir" "$pattern" "$DEPLOY_KEEP_DUMP_SETS" <<'PY'
import os, sys
from pathlib import Path

directory, pattern, keep = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
dumps = [p for p in directory.glob(pattern) if p.is_file() and not p.is_symlink()]
dumps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
for old in dumps[keep:]:
    for member in (old, old.with_suffix(".json") if old.suffix == ".dump" else None):
        if member is not None and member.is_file() and not member.is_symlink():
            member.unlink()
            print(f"Removed an old updater backup: {member.name}")
PY
    done
}
