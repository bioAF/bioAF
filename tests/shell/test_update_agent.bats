#!/usr/bin/env bats
# Tests for scripts/update-agent.sh

AGENT="$BATS_TEST_DIRNAME/../../scripts/update-agent.sh"
HOST_IO="$BATS_TEST_DIRNAME/../../scripts/update_host_io.py"

setup() {
    REPO="$(mktemp -d)"
    mkdir -p "$REPO/scripts" "$REPO/update-requests" "$REPO/update-status"
    cp "$AGENT" "$REPO/scripts/update-agent.sh"
    cp "$HOST_IO" "$REPO/scripts/update_host_io.py"
    printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
}

teardown() {
    chmod -R u+rwx "$REPO" 2>/dev/null || true
    rm -rf "$REPO"
}

@test "a failed update keeps the step and reason the update recorded" {
    printf '%s\n' '#!/usr/bin/env bash' \
        'dir="$(cd "$(dirname "$0")" && pwd)"' \
        "echo '{\"status\": \"failed\", \"to_version\": \"9.9.9\", \"step\": \"backup\", \"error\": \"Database backup failed\"}' > \"\$dir/update-status/current.json\"" \
        'exit 1' > "$REPO/bioaf"
    chmod +x "$REPO/bioaf"
    run bash -c "source '$REPO/scripts/update-agent.sh'; process_trigger '$REPO/update-requests/update_7.json'"
    grep -q '"step": "backup"' "$REPO/update-status/current.json"
    grep -q "Database backup failed" "$REPO/update-status/current.json"
    [ ! -e "$REPO/update-requests/update_7.json" ]
}

@test "a failure the update did not record is written as failed" {
    printf '%s\n' '#!/usr/bin/env bash' 'exit 3' > "$REPO/bioaf"
    chmod +x "$REPO/bioaf"
    run bash -c "source '$REPO/scripts/update-agent.sh'; process_trigger '$REPO/update-requests/update_7.json'"
    grep -q '"status": "failed"' "$REPO/update-status/current.json"
    grep -q "exited with code 3" "$REPO/update-status/current.json"
}

# ---------------------------------------------------------------------------
# The trigger boundary (plan 23). update-requests/ is writable from the backend container, so every
# name and payload there is untrusted. These run the actual agent with a stub updater.
# ---------------------------------------------------------------------------

BIOAF="$BATS_TEST_DIRNAME/../../bioaf"

# stub_updater [exit]: a ./bioaf that records its arguments, writes to stdout and stderr, and exits.
stub_updater() {
    cat > "$REPO/bioaf" <<STUB
#!/usr/bin/env bash
echo "\$*" >> '$REPO/updater-calls'
echo "UPDATER-STDOUT \$2"
echo "UPDATER-STDERR \$2" >&2
exit ${1:-0}
STUB
    chmod +x "$REPO/bioaf"
}

# agent_round [env...]: one polling round of the actual agent, its log opened as the service opens it,
# run from a scratch working directory.
agent_round() {
    mkdir -p "$REPO/cwd"
    run env "$@" bash -c "umask ${WRITE_UMASK:-022}; cd '$REPO/cwd' && source '$REPO/scripts/update-agent.sh' && open_agent_log && process_pending" </dev/null
}

# python_shim <body>: a python3 first on PATH that runs <body> (shell) and then the real interpreter.
python_shim() {
    local real
    real="$(command -v python3)"
    mkdir -p "$REPO/shim"
    printf '#!/usr/bin/env bash\n%s\nexec %q "$@"\n' "$1" "$real" > "$REPO/shim/python3"
    chmod +x "$REPO/shim/python3"
}

@test "a trigger filename is never run as code, and the next valid trigger is still processed" {
    stub_updater 0
    rm "$REPO/update-requests/update_7.json"
    hostile="a'+open('PWNED','w').write('x')+'.json"
    printf '{"version": "9.9.9"}' > "$REPO/update-requests/$hostile"
    printf '{"version": "9.9.8", "upgrade_id": 8}' > "$REPO/update-requests/update_8.json"
    agent_round
    [ ! -e "$REPO/cwd/PWNED" ]
    [ "$(cat "$REPO/updater-calls")" = "update 9.9.8 --yes" ]
    [ ! -e "$REPO/update-requests/$hostile" ]
    [ ! -e "$REPO/update-requests/update_8.json" ]
}

@test "a newline in a trigger name cannot forge an agent log line" {
    stub_updater 0
    rm "$REPO/update-requests/update_7.json"
    printf '{"version": "9.9.9"}' > "$REPO/update-requests/x"$'\n''[2026-01-01T00:00:00Z] FORGED.json'
    agent_round
    ! grep -q 'FORGED' "$REPO/host-only/agent.log"
    [[ "$output" != *"FORGED"* ]]
    [ ! -e "$REPO/updater-calls" ]
}

@test "malformed, non-object or mistyped triggers never run the updater or write a status" {
    stub_updater 0
    rm "$REPO/update-requests/update_7.json"
    local i=0 body
    for body in 'not json' '[1, 2]' '"9.9.9"' '{}' '{"version": 9}' '{"version": "9.9"}' \
        '{"version": "9.9.9; touch PWNED"}' '{"version": "9.9.9\n"}' '{"version": "9.9.9", "upgrade_id": "7"}' \
        '{"version": "9.9.9", "upgrade_id": true}' '{"version": "9.9.9", "upgrade_id": 1.5}' \
        '{"version": "9.9.9", "upgrade_id": "7\", \"x\": \"y"}' '{"version": "9.9.9", "requested_at": 5}'; do
        i=$((i + 1))
        printf '%s' "$body" > "$REPO/update-requests/update_$i.json"
    done
    agent_round
    [ "$status" -eq 0 ]
    [ ! -e "$REPO/updater-calls" ]
    [ ! -e "$REPO/cwd/PWNED" ]
    [ -z "$(ls -A "$REPO/update-requests")" ]
    [ ! -e "$REPO/update-status/current.json" ]
}

@test "the documented trigger fields are accepted, including the optional ones" {
    stub_updater 0
    printf '{"version": "9.9.9", "upgrade_id": 7, "requested_at": "2026-11-13T00:00:00+00:00"}' > "$REPO/update-requests/update_7.json"
    printf '{"version": "9.9.8"}' > "$REPO/update-requests/update_8.json"
    printf '{"version": "9.9.7", "upgrade_id": null}' > "$REPO/update-requests/update_9.json"
    agent_round
    [ "$(cat "$REPO/updater-calls")" = "$(printf 'update 9.9.9 --yes\nupdate 9.9.8 --yes\nupdate 9.9.7 --yes')" ]
    [ -z "$(ls -A "$REPO/update-requests")" ]
}

@test "the failure the agent records is valid JSON carrying the trigger's values" {
    stub_updater 4
    agent_round
    python3 - "$REPO/update-status/current.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["status"] == "failed" and d["to_version"] == "9.9.9" and d["upgrade_id"] == "7", d
assert "code 4" in d["error"] and d["completed_at"], d
PY
}

@test "a symlinked trigger is rejected without running the updater or touching its target" {
    stub_updater 0
    rm "$REPO/update-requests/update_7.json"
    printf '{"version": "9.9.9"}' > "$REPO/outside.json"
    ln -s "$REPO/outside.json" "$REPO/update-requests/update_7.json"
    agent_round
    [ ! -e "$REPO/updater-calls" ]
    [ ! -L "$REPO/update-requests/update_7.json" ]
    [ "$(cat "$REPO/outside.json")" = '{"version": "9.9.9"}' ]
}

@test "a FIFO trigger is rejected without blocking, and the next trigger is processed" {
    stub_updater 0
    mkfifo "$REPO/update-requests/update_1.json"
    mkdir -p "$REPO/cwd"
    run perl -e 'alarm 20; exec @ARGV' bash -c "cd '$REPO/cwd' && source '$REPO/scripts/update-agent.sh' && open_agent_log && process_pending" </dev/null
    [ "$status" -eq 0 ]
    [ "$(cat "$REPO/updater-calls")" = "update 9.9.9 --yes" ]
    [ ! -e "$REPO/update-requests/update_1.json" ]
}

@test "a trigger swapped for a symlink after any shell-side check is still rejected" {
    stub_updater 0
    printf '{"version": "9.9.1"}' > "$REPO/outside.json"
    python_shim "if [ -f '$REPO/update-requests/update_7.json' ] && [ ! -L '$REPO/update-requests/update_7.json' ]; then
rm '$REPO/update-requests/update_7.json'; ln -s '$REPO/outside.json' '$REPO/update-requests/update_7.json'; fi"
    agent_round PATH="$REPO/shim:$PATH"
    [ ! -e "$REPO/updater-calls" ]
    [ "$(cat "$REPO/outside.json")" = '{"version": "9.9.1"}' ]
}

@test "a trigger the parser fails on is skipped and the next valid trigger is processed" {
    stub_updater 0
    printf '{"version": "9.9.8"}' > "$REPO/update-requests/update_8.json"
    python_shim "if [ ! -e '$REPO/failed-once' ]; then : > '$REPO/failed-once'; exit 1; fi"
    agent_round PATH="$REPO/shim:$PATH"
    [ "$status" -eq 0 ]
    [ "$(cat "$REPO/updater-calls")" = "update 9.9.8 --yes" ]
    [ ! -e "$REPO/update-requests/update_7.json" ]
}

# ---------------------------------------------------------------------------
# Host status publication (plan 23). update-status/ is writable from the backend, so both host
# writers (the agent's fallback and ./bioaf's write_update_status) must never write through a link.
# ---------------------------------------------------------------------------

# write_status_with <agent|bioaf> [env...]: publish a failed status through one of the two writers.
write_status_with() {
    local writer="$1"; shift
    if [ "$writer" = agent ]; then
        stub_updater 3
        printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
        agent_round "$@"
    else
        run env "$@" bash -c "umask ${WRITE_UMASK:-022}; source '$BIOAF' >/dev/null; SCRIPT_DIR='$REPO'; _update_from=1.0.0; _update_to=9.9.9
            write_update_status failed backup 'it \"failed\"
here'" </dev/null
    fi
}

# published_ok: current.json is a regular file holding one complete JSON status record.
published_ok() {
    [ -f "$REPO/update-status/current.json" ] && [ ! -L "$REPO/update-status/current.json" ]
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["status"] == "failed", d' "$REPO/update-status/current.json"
}

@test "both status writers replace a symlinked current.json instead of writing through it" {
    local writer target
    for writer in agent bioaf; do
        for target in file dir; do
            rm -rf "$REPO/update-status/current.json" "$REPO/sentinel"
            if [ "$target" = file ]; then printf 'SENTINEL' > "$REPO/sentinel"; else mkdir "$REPO/sentinel"; printf 'S' > "$REPO/sentinel/keep"; fi
            ln -s "$REPO/sentinel" "$REPO/update-status/current.json"
            write_status_with "$writer"
            published_ok
            if [ "$target" = file ]; then [ "$(cat "$REPO/sentinel")" = SENTINEL ]; else [ "$(ls -A "$REPO/sentinel")" = keep ]; fi
        done
    done
}

@test "the bioaf writer encodes quotes and newlines in the error as JSON" {
    write_status_with bioaf
    [ "$status" -eq 0 ]
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["error"] == "it \"failed\"\nhere" and d["step"] == "backup" and d["to_version"] == "9.9.9", d' "$REPO/update-status/current.json"
}

@test "symlinks planted at temporary names in the status directory are never written through" {
    local writer name
    printf 'SENTINEL' > "$REPO/sentinel"
    for name in current.json.tmp .current.json.tmp current.json.new .current.json current.json.bioaf-staged; do
        ln -s "$REPO/sentinel" "$REPO/update-status/$name"
    done
    for writer in agent bioaf; do
        write_status_with "$writer"
        published_ok
        [ "$(cat "$REPO/sentinel")" = SENTINEL ]
    done
}

@test "a status directory swapped for a symlink is refused, not written through" {
    local writer
    for writer in agent bioaf; do
        rm -rf "$REPO/update-status" "$REPO/elsewhere"
        mkdir "$REPO/elsewhere"
        ln -s "$REPO/elsewhere" "$REPO/update-status"
        write_status_with "$writer"
        [ -z "$(ls -A "$REPO/elsewhere")" ]
    done
    # The bioaf writer reports the refusal.
    [ "$status" -ne 0 ]
}

@test "a failed replacement leaves the existing entry and no temporary files behind" {
    local writer
    for writer in agent bioaf; do
        rm -rf "$REPO/update-status/current.json"
        mkdir -p "$REPO/update-status/current.json"
        printf 'S' > "$REPO/update-status/current.json/keep"
        write_status_with "$writer"
        [ "$(ls -A "$REPO/update-status/current.json")" = keep ]
        [ -z "$(ls -A "$REPO/host-state" | grep -v '^agent-code$' || true)" ]
    done
    [ "$status" -ne 0 ]
}

# fail_python_op <os function>: a python3 shim whose program has that os function raise OSError.
fail_python_op() {
    local real
    real="$(command -v python3)"
    mkdir -p "$REPO/shim"
    cat > "$REPO/shim/python3" <<STUB
#!/usr/bin/env bash
args=(); prev=""
for a in "\$@"; do
    if [ "\$prev" = -c ]; then
        a="import os
def _injected(*a, **k): raise OSError('injected failure')
os.$1 = _injected
\$a"
    fi
    args+=("\$a"); prev="\$a"
done
exec '$real' "\${args[@]}"
STUB
    chmod +x "$REPO/shim/python3"
}

@test "an interrupted publication leaves the previous complete record in place" {
    local writer op
    for op in fchmod replace; do
        fail_python_op "$op"
        for writer in agent bioaf; do
            printf '{"status": "in_progress", "step": "pull"}\n' > "$REPO/update-status/current.json"
            write_status_with "$writer" PATH="$REPO/shim:$PATH"
            [ "$(cat "$REPO/update-status/current.json")" = '{"status": "in_progress", "step": "pull"}' ]
            [ -z "$(ls -A "$REPO/host-state" 2>/dev/null | grep -v '^agent-code$' || true)" ]
        done
        [ "$status" -ne 0 ]
    done
}

@test "both writers publish current.json at 0644 under a restrictive umask" {
    local writer
    for writer in agent bioaf; do
        rm -f "$REPO/update-status/current.json"
        if [ "$writer" = agent ]; then
            stub_updater 3
            printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
            mkdir -p "$REPO/cwd"
            run bash -c "umask 077; cd '$REPO/cwd' && source '$REPO/scripts/update-agent.sh' && open_agent_log && process_pending" </dev/null
        else
            run bash -c "umask 077; source '$BIOAF' >/dev/null; SCRIPT_DIR='$REPO'; _update_from=1; _update_to=2; write_update_status failed backup x" </dev/null
        fi
        [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/update-status/current.json")" = 0o644 ]
    done
}

# as_other_user <cmd...>: run as a different non-root user (nobody), or fail when that is not possible.
as_other_user() {
    if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1; then runuser -u nobody -- "$@"
    elif sudo -n true 2>/dev/null; then sudo -n -u nobody "$@"
    else return 99; fi
}

@test "a status directory a writer creates is open to the backend's reader even under umask 077" {
    local writer
    for writer in agent bioaf; do
        rm -rf "$REPO/update-status"
        WRITE_UMASK=077 write_status_with "$writer"
        [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/update-status")" = 0o755 ]
        [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/update-status/current.json")" = 0o644 ]
    done
}

@test "a different non-root user reads and parses the published status" {
    if ! as_other_user true; then skip "cannot switch to another user here (needs root or passwordless sudo)"; fi
    # Only the checkout is opened up (mktemp makes it 0700); the container sees update-status/ itself,
    # which each writer creates here under a restrictive umask.
    chmod 755 "$REPO"
    local writer
    for writer in agent bioaf; do
        rm -rf "$REPO/update-status"
        WRITE_UMASK=077 write_status_with "$writer"
        run as_other_user python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$REPO/update-status/current.json"
        [ "$status" -eq 0 ]
        [ "$output" = failed ]
    done
}

@test "status staging works when the private log directory is inaccessible" {
    [ "$(id -u)" != 0 ] || skip "root ignores directory permissions"
    mkdir -p "$REPO/host-only"
    chmod 000 "$REPO/host-only"
    local writer
    for writer in agent bioaf; do
        rm -f "$REPO/update-status/current.json"
        write_status_with "$writer"
        published_ok
    done
    chmod 700 "$REPO/host-only"
}

@test "an unsafe staging directory fails publication and never falls back to the shared directory" {
    local writer kind
    for kind in symlink group-writable; do
        rm -rf "$REPO/host-state" "$REPO/elsewhere" "$REPO/update-status"/*
        if [ "$kind" = symlink ]; then
            mkdir -m 700 "$REPO/elsewhere"; ln -s "$REPO/elsewhere" "$REPO/host-state"
        else
            mkdir "$REPO/host-state"; chmod 770 "$REPO/host-state"
        fi
        for writer in agent bioaf; do
            write_status_with "$writer"
            [ ! -e "$REPO/update-status/current.json" ]
            [ -z "$(ls -A "$REPO/update-status" | grep -v '^agent.log$' || true)" ]
            [ -z "$(ls -A "$REPO/elsewhere" 2>/dev/null || true)" ]
        done
        [ "$status" -ne 0 ]
    done
}

@test "the agent's own status read ignores a symlinked or non-regular current.json" {
    stub_updater 3
    printf '{"status": "failed", "step": "backup"}' > "$REPO/recorded.json"
    ln -s "$REPO/recorded.json" "$REPO/update-status/current.json"
    agent_round
    # Not taken as the update's own recorded failure: the agent publishes its own.
    published_ok
    grep -q 'code 3' "$REPO/update-status/current.json"
    [ "$(cat "$REPO/recorded.json")" = '{"status": "failed", "step": "backup"}' ]
}

# ---------------------------------------------------------------------------
# The agent's private log (plan 23): host-only/agent.log when it can be opened safely, otherwise the
# service's inherited stdout/stderr (the journal under systemd). Never the shared update-status/.
# ---------------------------------------------------------------------------

@test "agent messages and updater output go to a private host-only log" {
    stub_updater 0
    agent_round
    [ "$status" -eq 0 ]
    grep -q 'Update to 9.9.9 completed successfully' "$REPO/host-only/agent.log"
    grep -q 'UPDATER-STDOUT 9.9.9' "$REPO/host-only/agent.log"
    grep -q 'UPDATER-STDERR 9.9.9' "$REPO/host-only/agent.log"
    [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/host-only")" = 0o700 ]
    [ "$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/host-only/agent.log")" = 0o600 ]
    [ ! -e "$REPO/update-status/agent.log" ]
}

@test "a symlink planted at the old shared agent.log is never opened" {
    stub_updater 0
    printf 'SENTINEL\n' > "$REPO/sentinel"
    ln -s "$REPO/sentinel" "$REPO/update-status/agent.log"
    agent_round
    [ "$(cat "$REPO/sentinel")" = SENTINEL ]
    [ -L "$REPO/update-status/agent.log" ]
}

# unsafe_log <kind>: make the private log unusable in one way.
unsafe_log() {
    case "$1" in
        log-symlink) mkdir -m 700 "$REPO/host-only"; printf 'SENTINEL\n' > "$REPO/sentinel"; ln -s "$REPO/sentinel" "$REPO/host-only/agent.log" ;;
        dir-symlink) mkdir -m 700 "$REPO/elsewhere"; ln -s "$REPO/elsewhere" "$REPO/host-only" ;;
        dir-inaccessible) mkdir "$REPO/host-only"; chmod 000 "$REPO/host-only" ;;
        dir-readonly) mkdir "$REPO/host-only"; chmod 500 "$REPO/host-only" ;;
        dir-group-writable) mkdir "$REPO/host-only"; chmod 770 "$REPO/host-only" ;;
        log-denied) mkdir -m 700 "$REPO/host-only"; : > "$REPO/host-only/agent.log"; chmod 000 "$REPO/host-only/agent.log" ;;
        log-fifo) mkdir -m 700 "$REPO/host-only"; mkfifo "$REPO/host-only/agent.log" ;;
    esac
}

@test "an unusable private log falls back to the service streams and keeps processing triggers" {
    [ "$(id -u)" != 0 ] || skip "root ignores file permissions"
    local kind
    for kind in log-symlink dir-symlink dir-inaccessible dir-readonly dir-group-writable log-denied log-fifo; do
        chmod -R u+rwx "$REPO/host-only" 2>/dev/null || true
        rm -rf "$REPO/host-only" "$REPO/elsewhere" "$REPO/sentinel" "$REPO/updater-calls" "$REPO/update-status"/*
        unsafe_log "$kind"
        local before
        before="$(ls -ld "$REPO/host-only" | cut -c1-10)"
        stub_updater 5
        printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
        printf '{"version": "9.9.8", "upgrade_id": 8}' > "$REPO/update-requests/update_8.json"
        run perl -e 'alarm 20; exec @ARGV' bash -c "cd '$REPO' && source '$REPO/scripts/update-agent.sh' && open_agent_log && process_pending" </dev/null
        [ "$status" -eq 0 ]
        [[ "$output" == *"private log"* ]]
        [[ "$output" == *"UPDATER-STDOUT 9.9.9"* ]] && [[ "$output" == *"UPDATER-STDERR 9.9.8"* ]]
        [[ "$output" == *"failed with exit code 5"* ]]
        [ "$(cat "$REPO/updater-calls")" = "$(printf 'update 9.9.9 --yes\nupdate 9.9.8 --yes')" ]
        grep -q 'code 5' "$REPO/update-status/current.json"
        [ ! -e "$REPO/update-status/agent.log" ]
        [ "$(ls -ld "$REPO/host-only" | cut -c1-10)" = "$before" ]
        if [ -e "$REPO/sentinel" ]; then [ "$(cat "$REPO/sentinel")" = SENTINEL ]; fi
        if [ -d "$REPO/elsewhere" ]; then [ -z "$(ls -A "$REPO/elsewhere")" ]; fi
    done
    chmod -R u+rwx "$REPO/host-only" 2>/dev/null || true
}
