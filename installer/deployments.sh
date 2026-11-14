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
