#!/usr/bin/env bats
# The deployment record and the install-wide lock (installer/deployments.sh, issue #110, stages A/B).
#
# Every update, build and setup records which backend and frontend images run, by immutable image ID, as the
# current generation, and the one before as the previous generation. A tag is not identity: a same-tag rebuild or
# a one-service build is a new generation. Writes are atomic and private. An operation in progress is recorded as
# the candidate until the new images run, so an interrupted operation is visible to the next one. Only one
# deployment-changing operation runs at a time. Nothing is deleted at these stages.

LIB="$BATS_TEST_DIRNAME/../../installer/deployments.sh"

setup() {
    WORK="$(mktemp -d)"
    export SCRIPT_DIR="$WORK"
    export FAKE_IDS="$WORK/ids"
    mkdir -p "$FAKE_IDS"
    # docker compose ps -q <svc> prints a container id; docker inspect prints its image id (or fails).
    cat > "$WORK/fake-dc" <<'STUB'
#!/usr/bin/env bash
case "$*" in
    *"ps -q backend"*) [ -s "$FAKE_IDS/backend" ] && echo "cid-backend" ;;
    *"ps -q frontend"*) [ -s "$FAKE_IDS/frontend" ] && echo "cid-frontend" ;;
esac
exit 0
STUB
    cat > "$WORK/fake-docker" <<'STUB'
#!/usr/bin/env bash
[ "$1" = inspect ] || exit 1
svc="${*: -1}"; svc="${svc#cid-}"
[ -s "$FAKE_IDS/$svc" ] || exit 1
cat "$FAKE_IDS/$svc"
STUB
    chmod +x "$WORK/fake-dc" "$WORK/fake-docker"
    export DC="$WORK/fake-dc" DEPLOY_DOCKER="$WORK/fake-docker"
}

teardown() {
    [ -n "${HOLDER:-}" ] && { pkill -P "$HOLDER" 2>/dev/null; kill "$HOLDER" 2>/dev/null; } || true
    rm -rf "$WORK"
}

running() { echo "sha256:$1" > "$FAKE_IDS/backend"; echo "sha256:$2" > "$FAKE_IDS/frontend"; }
state() { python3 -c "import json,sys; d=json.load(open('$WORK/host-state/deployments.json')); print(eval(sys.argv[1]))" "$1"; }
lib() { bash -c "source '$LIB'; $*"; }

@test "the first record makes the running pair the current generation, privately" {
    running b1 f1
    run lib deploy_record_running v1.0.0
    [ "$status" -eq 0 ]
    [ "$(state "d['current']['backend']")" = "sha256:b1" ]
    [ "$(state "d['current']['frontend']")" = "sha256:f1" ]
    [ "$(state "d['current']['tag']")" = "v1.0.0" ]
    [ "$(state "d['previous']")" = "None" ]
    [ "$(stat -f %Lp "$WORK/host-state" 2>/dev/null || stat -c %a "$WORK/host-state")" = 700 ]
    [ "$(stat -f %Lp "$WORK/host-state/deployments.json" 2>/dev/null || stat -c %a "$WORK/host-state/deployments.json")" = 600 ]
}

@test "a new pair becomes current and the old current becomes previous" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b2 f2; lib deploy_record_running v1.1.0
    [ "$(state "d['current']['backend']")" = "sha256:b2" ]
    [ "$(state "d['previous']['backend']")" = "sha256:b1" ]
    [ "$(state "d['previous']['tag']")" = "v1.0.0" ]
}

@test "recording the same pair again changes nothing" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b2 f2; lib deploy_record_running v1.1.0
    before="$(cat "$WORK/host-state/deployments.json")"
    lib deploy_record_running v1.1.0
    [ "$(cat "$WORK/host-state/deployments.json")" = "$before" ]
}

@test "a same-tag rebuild is a new generation; the tag is not its identity" {
    running b1 f1; lib deploy_record_running v1.0.0
    running b1x f1; lib deploy_record_running v1.0.0
    [ "$(state "d['current']['backend']")" = "sha256:b1x" ]
    [ "$(state "d['previous']['backend']")" = "sha256:b1" ]
    [ "$(state "d['current']['frontend']")" = "sha256:f1" ]
}

@test "a missing running service records nothing" {
    running b1 f1; lib deploy_record_running v1.0.0
    rm "$FAKE_IDS/frontend"
    before="$(cat "$WORK/host-state/deployments.json")"
    run lib deploy_record_running v2.0.0
    [ "$status" -ne 0 ]
    [ "$(cat "$WORK/host-state/deployments.json")" = "$before" ]
}

@test "a candidate stays recorded until its images run, so an interruption is visible" {
    running b1 f1; lib deploy_record_running v1.0.0
    lib deploy_mark_candidate update v1.1.0
    [ "$(state "d['candidate']['tag']")" = "v1.1.0" ]
    [ "$(state "d['candidate']['operation']")" = "update" ]
    run lib deploy_interrupted
    [ "$status" -eq 0 ]
    [[ "$output" == *"update to v1.1.0"* ]]
    running b2 f2; lib deploy_record_running v1.1.0
    [ "$(state "d['candidate']")" = "None" ]
    run lib deploy_interrupted
    [ "$status" -ne 0 ]
}

@test "a damaged record is never overwritten as if it were empty" {
    mkdir -m 700 "$WORK/host-state"
    echo "{not json" > "$WORK/host-state/deployments.json"
    running b1 f1
    run lib deploy_record_running v1.0.0
    [ "$status" -ne 0 ]
    [ "$(cat "$WORK/host-state/deployments.json")" = "{not json" ]
}

@test "a second deployment operation is refused while one holds the lock" {
    mkdir -m 700 "$WORK/host-state"
    bash -c "source '$LIB'; deploy_lock && sleep 30" &
    HOLDER=$!
    for _ in $(seq 1 50); do
        [ -e "$WORK/host-state/deployment.lock" ] && break
        sleep 0.1
    done
    sleep 0.5
    run lib deploy_lock
    [ "$status" -eq 75 ]
    pkill -P "$HOLDER" 2>/dev/null || true; kill "$HOLDER"; wait "$HOLDER" 2>/dev/null || true
    HOLDER=""
    run lib deploy_lock
    [ "$status" -eq 0 ]
}
