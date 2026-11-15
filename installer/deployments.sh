#!/usr/bin/env bash
# Deployment generations and the install-wide deployment lock (issue #110, stages A and B).
#
# Sourced by ./bioaf. Every operation that changes what runs (update, build, setup) takes the lock first, so
# two of them never overlap, and records generations in host-state/deployments.json:
#
#   current    the backend and frontend images running now, by immutable image ID, with the tag they ran under
#   previous   the generation before it (the rollback images)
#   local_tag  on current and previous: gen-<backend12>-<frontend12>, a tag both of the generation's images carry
#              (deploy_sync_generation_tags). An image ID does not keep an image: Docker's containerd store deletes
#              one as soon as its last tag moves, even while it runs. The tag keeps it, and names it for a rollback
#              (BIOAF_IMAGE_TAG=<local_tag>). images_present says whether both images were on the host when last
#              checked; a generation without them is kept in the record (its recovery set) but is no rollback.
#   candidate  an operation that started and has not yet run its images (update, build or setup, and its tag);
#              still present at the next operation means that one was interrupted or failed
#   update_operation
#              the latest update's own identity (ADR-075): id (a random UUID), target tag, from_version (the
#              source when it first started), started_at, status (in_progress | completed) and completed_at,
#              written once. A retry of the same unfinished target keeps id, start and source, even after the
#              target image started and the candidate was cleared; a different target, or any update after a
#              completed one, is a new operation. Telemetry only: no deployment decision reads it except
#              whether an interrupted update to the running target still needs its readiness checks.
#   recovery   on a generation an update installed: the updater backups made since the first unfinished operation
#              before it started (the database as the generation before had it, and any partial attempt's).
#              Retention never removes current's or previous's set, nor anything made since a pending
#              candidate's protect_since: an unfinished or retried update keeps its backups.
#
# A tag is not identity: a same-tag rebuild or a one-service build is a new generation. The record is written
# atomically (a reader never sees half a file) and privately (0700 directory, 0600 file). A damaged record is
# reported and left as it is, never replaced as if empty. Images are removed only by retention (stage C below):
# deploy_prune_images, and deploy_sync_generation_tags removing a generation tag the record no longer names.
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
    python3 -I - "$(deploy_state_dir)/deployments.json" "$SCRIPT_DIR/backups" "$@" <<'PY'
import datetime, json, os, sys, tempfile
from pathlib import Path

path, backups, change, *args = sys.argv[1:]
output = None
UPDATER_BACKUPS = ("bioaf_pre_update_*.sql.gz", "bioaf_pre_upgrade_*.dump", "bioaf_pre_upgrade_*.json")


def made_since(started_at):
    """Updater backups written since ``started_at`` (ISO seconds): what this update's database state is."""
    since = datetime.datetime.fromisoformat(started_at).timestamp()
    found = set()
    for pattern in UPDATER_BACKUPS:
        for p in Path(backups).glob(pattern):
            if p.is_file() and not p.is_symlink() and p.stat().st_mtime >= since:
                found.add(p.name)
    return sorted(found)


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

def local_tag(generation):
    """The generation's own tag: gen-<backend12>-<frontend12> (image IDs without their sha256: prefix)."""
    short = lambda image_id: image_id.split(":")[-1][:12]
    return f"gen-{short(generation['backend'])}-{short(generation['frontend'])}"


if change == "candidate":
    operation, tag = args
    earlier = record.get("candidate") or {}
    if earlier.get("operation") == operation and earlier.get("tag") == tag:
        sys.exit(0)  # a retry of the same operation keeps its first start (its backups stay attributable)
    # Replacing an unfinished operation keeps protecting what it backed up: the database may be partly upgraded.
    since = earlier.get("protect_since") or earlier.get("started_at") or now
    record["candidate"] = {"operation": operation, "tag": tag, "started_at": now, "protect_since": since}
elif change == "running":
    tag, backend, frontend = args
    current = record.get("current")
    if current and current.get("backend") == backend and current.get("frontend") == frontend:
        if record.get("candidate") is None:
            sys.exit(0)  # nothing changed: the file is not rewritten
    elif (previous := record.get("previous")) and (previous.get("backend"), previous.get("frontend")) == (
        backend,
        frontend,
    ):
        # A rollback: the previous generation runs again. The two swap, each keeping its tag and recovery set.
        record["current"], record["previous"] = previous, current
    else:
        candidate = record.get("candidate") or {}
        since = candidate.get("protect_since") or candidate.get("started_at")
        recovery = made_since(since) if since else []
        record["previous"] = current
        record["current"] = {
            "tag": tag, "backend": backend, "frontend": frontend, "recorded_at": now, "recovery": recovery,
        }
        record["current"]["local_tag"] = local_tag(record["current"])
    record["candidate"] = None
elif change == "operation-start":
    import uuid

    tag, source = args
    op = record.get("update_operation") or {}
    if op.get("status") == "in_progress" and op.get("target") == tag:
        print(op["id"], op["started_at"], op["from_version"] or "-")
        sys.exit(0)  # a retry of the same unfinished update: unchanged
    op = {
        "id": str(uuid.uuid4()), "target": tag, "from_version": source, "started_at": now,
        "status": "in_progress", "completed_at": None,
    }
    record["update_operation"] = op
    output = f"{op['id']} {op['started_at']} {source or '-'}"
elif change == "operation-complete":
    (tag,) = args
    op = record.get("update_operation") or {}
    if op.get("target") != tag or op.get("status") not in ("in_progress", "completed"):
        sys.exit(1)
    if op["status"] == "completed":
        print(op["completed_at"])
        sys.exit(0)  # completion is written once
    op["status"], op["completed_at"] = "completed", now
    output = now
elif change == "presence":
    # args: <generation>=<1|0> for each generation checked; also names a generation recorded before local tags.
    before = json.dumps(record, sort_keys=True)
    for item in args:
        generation, _, present = item.partition("=")
        entry = record.get(generation)
        if entry:
            entry.setdefault("local_tag", local_tag(entry))
            entry["images_present"] = present == "1"
    if json.dumps(record, sort_keys=True) == before:
        sys.exit(0)
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
if output is not None:
    print(output)
PY
}

deploy_mark_candidate() {
    # Record that <operation> toward <tag> started; it stays recorded until deploy_record_running sees it run.
    _deploy_update candidate "$1" "$2"
}

deploy_operation_start() {
    # deploy_operation_start <tag> <source version>: create this update's operation, or resume the unfinished one
    # toward the same tag. Prints "<id> <started_at> <source version>" ("-" for an unknown source).
    _deploy_update operation-start "$1" "$2"
}

deploy_operation_complete() {
    # deploy_operation_complete <tag>: mark the operation toward <tag> completed; prints its completion time.
    _deploy_update operation-complete "$1"
}

deploy_operation_unfinished() {
    # deploy_operation_unfinished <tag>: true when an update toward <tag> started and never completed.
    local path
    path="$(deploy_state_dir)/deployments.json"
    [ -f "$path" ] || return 1
    python3 -I - "$path" "$1" <<'PY'
import json, sys
try:
    op = json.load(open(sys.argv[1])).get("update_operation") or {}
except (ValueError, AttributeError):
    sys.exit(1)
sys.exit(0 if op.get("status") == "in_progress" and op.get("target") == sys.argv[2] else 1)
PY
}

deploy_record_running() {
    # Record the running backend and frontend images, under <tag>, as the current generation (the old current
    # becomes previous; the same pair changes nothing). Fails, recording nothing, if a service is not running.
    local tag="$1" ids
    ids="$(deploy_running_ids)" || return 1
    # shellcheck disable=SC2086  # two words: the backend and frontend image IDs
    _deploy_update running "$tag" $ids
}

deploy_record_generation() {
    # deploy_record_generation <tag>: record the running images as the current generation, then tag the generations.
    deploy_record_running "$1" || return 1
    deploy_sync_generation_tags
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

deploy_sync_generation_tags() {
    # Give the current and previous generations' images their local tag (see the header), record whether each
    # generation's images are on the host, and remove the local tags of generations that left the record (Docker
    # then frees or prunes those images). Never removes the pinned BIOAF_IMAGE_TAG or any other tag.
    local path generations gen tag backend frontend svc id present ref pinned wanted="" presence=()
    path="$(deploy_state_dir)/deployments.json"
    [ -f "$path" ] || return 0
    if ! generations="$(python3 -I - "$path" <<'PY'
import json, sys
try:
    record = json.load(open(sys.argv[1]))
except ValueError:
    sys.exit(1)
for generation in ("current", "previous"):
    g = record.get(generation) or {}
    if g.get("backend") and g.get("frontend"):
        short = lambda image_id: image_id.split(":")[-1][:12]
        print(generation, f"gen-{short(g['backend'])}-{short(g['frontend'])}", g["backend"], g["frontend"])
PY
)"; then
        yellow "The deployment record could not be read, so the generations' image tags were left as they are."
        return 0
    fi
    while read -r gen tag backend frontend; do
        [ -n "$gen" ] || continue
        wanted+="$tag"$'\n'
        present=1
        for svc in $DEPLOY_SERVICES; do
            if [ "$svc" = backend ]; then id="$backend"; else id="$frontend"; fi
            _deploy_docker image inspect "$id" >/dev/null 2>&1 \
                && _deploy_docker tag "$id" "${DEPLOY_IMAGE_REPO_PREFIX}${svc}:${tag}" >/dev/null 2>&1 \
                || present=0
        done
        presence+=("$gen=$present")
        [ "$present" = 1 ] \
            || yellow "The $gen generation's images ($tag) are no longer on this host, so bioAF cannot roll back to them."
    done <<< "$generations"
    [ "${#presence[@]}" -eq 0 ] || _deploy_update presence "${presence[@]}" || return 1
    pinned="$(grep '^BIOAF_IMAGE_TAG=' "${ENV_FILE:-$SCRIPT_DIR/docker/.env}" 2>/dev/null | tail -n 1 | cut -d= -f2-)" || pinned=""
    for ref in $(_deploy_docker images --filter "label=$DEPLOY_IMAGE_LABEL" --format '{{.Repository}}:{{.Tag}}' 2>/dev/null); do
        case "$ref" in "${DEPLOY_IMAGE_REPO_PREFIX}"*:gen-*) ;; *) continue ;; esac
        tag="${ref##*:}"
        if ! grep -qxF "$tag" <<< "$wanted" && [ "$tag" != "$pinned" ]; then
            _deploy_docker rmi "$ref" >/dev/null 2>&1 \
                || yellow "An old generation's image tag ($ref) could not be removed; it was left in place."
        fi
    done
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

deploy_report_build_cache() {
    # Report a build cache over DEPLOY_BUILD_CACHE_BYTES; never prune it. The cache belongs to the host's shared
    # default builder, and Docker cannot prune one project's cache there, so removing it is the operator's call
    # (owner decision on review finding 6). Prints nothing when within budget or when it cannot be measured.
    local size
    size="$(_deploy_docker system df --format '{{if eq .Type "Build Cache"}}{{.Size}}{{end}}' 2>/dev/null)" || return 0
    python3 -I - "$size" "$DEPLOY_BUILD_CACHE_BYTES" <<'PY'
import re, sys

text, budget = sys.argv[1].strip(), int(sys.argv[2])
units = {"": 1, "B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}
m = re.fullmatch(r"([0-9.]+)\s*([kKMGT]?B)?", text)
if not m:
    sys.exit(0)
used = float(m.group(1)) * units[m.group(2) or ""]
if used > budget:
    print(
        f"The Docker build cache uses {used / 2**30:.1f} GiB, over bioAF's {budget / 2**30:.0f} GiB budget. It is "
        "shared with everything else built on this host, so bioAF does not remove it. To free it: "
        f"docker builder prune --filter until=168h"
    )
PY
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
    # ./bioaf update, bioaf_pre_upgrade_*.dump with its .json manifest from the database upgrade), after first
    # protecting the recovery sets of the current and previous generations (the backups a rollback needs).
    # Without a readable record nothing is removed. Manual backups, other names and links are never touched.
    local dir="$SCRIPT_DIR/backups"
    [ -d "$dir" ] || return 0
    python3 -I - "$dir" "$(deploy_state_dir)/deployments.json" "$DEPLOY_KEEP_DUMP_SETS" <<'PY'
import datetime, json, sys
from pathlib import Path

directory, record_path, keep = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
try:
    record = json.load(open(record_path))
    protected = set()
    for generation in ("current", "previous"):
        protected.update((record.get(generation) or {}).get("recovery") or [])
    candidate = record.get("candidate") or {}
    since = candidate.get("protect_since") or candidate.get("started_at")
    pending_since = datetime.datetime.fromisoformat(since).timestamp() if since else None
except (OSError, ValueError, AttributeError, TypeError):
    print("The deployment record could not be read, so old updater backups were left in place.", file=sys.stderr)
    sys.exit(0)
for pattern in ("bioaf_pre_update_*.sql.gz", "bioaf_pre_upgrade_*.dump"):
    dumps = [
        p
        for p in directory.glob(pattern)
        if p.is_file()
        and not p.is_symlink()
        and p.name not in protected
        and (pending_since is None or p.stat().st_mtime < pending_since)  # a pending update's backups stay
    ]
    dumps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[keep:]:
        for member in (old, old.with_suffix(".json") if old.suffix == ".dump" else None):
            if member is not None and member.is_file() and not member.is_symlink() and member.name not in protected:
                member.unlink()
                print(f"Removed an old updater backup: {member.name}")
PY
}
