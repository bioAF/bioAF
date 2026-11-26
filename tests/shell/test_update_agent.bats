#!/usr/bin/env bats
# Tests for scripts/update-agent.sh

AGENT="$BATS_TEST_DIRNAME/../../scripts/update-agent.sh"
HOST_IO="$BATS_TEST_DIRNAME/../../scripts/update_host_io.py"


# absent <grep args>: fails the test when the pattern is found (a bare "! grep" mid-test never fails it).
absent() { if grep -q "$@"; then return 1; fi; }

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

@test "an update refused because another one is running keeps its trigger and the running status" {
    printf '%s\n' '#!/usr/bin/env bash' 'exit 75' > "$REPO/bioaf"
    chmod +x "$REPO/bioaf"
    mkdir -p "$REPO/update-status"
    printf '{"status": "in_progress", "step": "pull"}' > "$REPO/update-status/current.json"
    run bash -c "source '$REPO/scripts/update-agent.sh'; process_trigger '$REPO/update-requests/update_7.json'"
    [ -e "$REPO/update-requests/update_7.json" ]
    grep -q '"status": "in_progress"' "$REPO/update-status/current.json"
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

mode_of() { python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$1"; }

@test "an existing log readable by others is tightened to 0700/0600 before it is used" {
    local dir_mode file_mode
    for dir_mode in 755 750 711; do
        for file_mode in 644 640 604; do
            rm -rf "$REPO/host-only" "$REPO/updater-calls"
            mkdir "$REPO/host-only"; chmod "$dir_mode" "$REPO/host-only"
            printf 'old line\n' > "$REPO/host-only/agent.log"; chmod "$file_mode" "$REPO/host-only/agent.log"
            stub_updater 0
            printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
            agent_round
            [ "$(mode_of "$REPO/host-only")" = 0o700 ]
            [ "$(mode_of "$REPO/host-only/agent.log")" = 0o600 ]
            grep -q 'UPDATER-STDOUT 9.9.9' "$REPO/host-only/agent.log"
            [[ "$output" != *"private log"* ]]
        done
    done
}

@test "a log file others could have written is not used or changed" {
    local file_mode
    for file_mode in 662 646 666; do
        rm -rf "$REPO/host-only" "$REPO/updater-calls"
        mkdir -m 700 "$REPO/host-only"
        : > "$REPO/host-only/agent.log"; chmod "$file_mode" "$REPO/host-only/agent.log"
        stub_updater 0
        printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
        agent_round
        [[ "$output" == *"private log"* ]]
        [ "$(mode_of "$REPO/host-only/agent.log")" = "0o$file_mode" ]
        [ ! -s "$REPO/host-only/agent.log" ]
        [ "$(cat "$REPO/updater-calls")" = "update 9.9.9 --yes" ]
    done
}

@test "the private log rotates past its size limit, keeping two old files" {
    stub_updater 0
    rm -f "$REPO/update-requests/update_7.json"
    mkdir -m 700 -p "$REPO/host-only"
    for round in 1 2 3; do
        head -c 200 /dev/zero | tr '\0' 'x' > "$REPO/host-only/agent.log"; chmod 600 "$REPO/host-only/agent.log"
        echo "round-$round" >> "$REPO/host-only/agent.log"
        agent_round AGENT_LOG_MAX_BYTES=100
        [ "$status" -eq 0 ]
    done
    grep -q "round-3" "$REPO/host-only/agent.log.1"
    grep -q "round-2" "$REPO/host-only/agent.log.2"
    [ ! -e "$REPO/host-only/agent.log.3" ]
    [ "$(python3 -c 'import os, sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777))' "$REPO/host-only/agent.log.1")" = 0o600 ]
    ! grep -q "round" "$REPO/host-only/agent.log"
}

# ---------------------------------------------------------------------------
# Host python3 prerequisite. Every trigger read and status write runs python3, so the agent checks it at
# startup and stops, with a diagnostic in the journal, instead of polling triggers it cannot handle.
# ---------------------------------------------------------------------------

load host_python

# start_agent <python kind>: start the actual agent as the service does (no terminal, its own working
# directory) with only that python3, give it up to 5 seconds to exit, then stop it. Prints STILL-RUNNING
# and fails with 99 when it was still running.
start_agent() {
    mkdir -p "$REPO/cwd"
    mkdir -p "$REPO/installer"
    cp "$BATS_TEST_DIRNAME/../../installer/host_prereqs.sh" "$REPO/installer/"
    local path
    path="$(host_python_path "$BATS_FILE_TMPDIR/py" "$1")"
    run env PATH="$path" bash -c "cd '$REPO/cwd'
        bash '$REPO/scripts/update-agent.sh' </dev/null & pid=\$!
        for _ in \$(seq 50); do kill -0 \$pid 2>/dev/null || break; sleep 0.1; done
        if kill -0 \$pid 2>/dev/null; then kill \$pid; echo STILL-RUNNING; exit 99; fi
        wait \$pid"
}

@test "the agent stops at startup without a usable python3, leaving triggers and status untouched" {
    stub_updater
    printf '{"status": "completed", "to_version": "2026.11.20"}' > "$REPO/update-status/current.json"
    cp "$REPO/update-requests/update_7.json" "$REPO/trigger.before"
    cp "$REPO/update-status/current.json" "$REPO/status.before"
    for kind in none broken old nocap; do
        start_agent "$kind"
        [ "$status" -ne 0 ] && [ "$status" -ne 99 ]
        [[ "$output" == *"python3"* ]]
        [[ "$output" == *"3.10"* ]]
        [[ "$output" == *"journalctl -u bioaf-update-agent"* ]]
        cmp "$REPO/trigger.before" "$REPO/update-requests/update_7.json"
        cmp "$REPO/status.before" "$REPO/update-status/current.json"
        [ ! -e "$REPO/updater-calls" ]
        [ ! -e "$REPO/host-only" ] && [ ! -e "$REPO/host-state" ]
    done
}

@test "the agent without python3 creates none of its directories" {
    rm -rf "$REPO/update-requests" "$REPO/update-status"
    start_agent none
    [ "$status" -ne 0 ] && [ "$status" -ne 99 ]
    [ ! -e "$REPO/update-requests" ] && [ ! -e "$REPO/update-status" ]
    [ ! -e "$REPO/host-only" ] && [ ! -e "$REPO/host-state" ]
}

@test "the agent with a working python3 starts and keeps polling" {
    stub_updater
    start_agent real
    [ "$status" -eq 99 ]
    [ -s "$REPO/host-state/agent-code" ]
    grep -q "Update agent started" "$REPO/host-only/agent.log"
}

@test "a change to the host prerequisite check marks the running agent's code as out of date" {
    mkdir -p "$REPO/installer"
    cp "$BATS_TEST_DIRNAME/../../installer/host_prereqs.sh" "$REPO/installer/"
    python3 -I "$HOST_IO" record-agent-code "$REPO"
    python3 -I "$HOST_IO" agent-code-current "$REPO"
    echo '# policy change' >> "$REPO/installer/host_prereqs.sh"
    run python3 -I "$HOST_IO" agent-code-current "$REPO"
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# One-time updater preparation (plan 03A). An older updater runs the target's install.sh hook, which
# prepares only the host update helper and refuses; the older updater then records its own fixed
# error. The prepared agent rewrites that one failure to the plain retry instruction, but only when
# the host-owned preparation record (host-state/, outside every backend mount) matches it.
# ---------------------------------------------------------------------------

PREPARED_TEXT="The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue."

status_json() {
    printf '%s\n' "$1" > "$REPO/update-status/current.json"
}

@test "a matching preparation record turns the older updater's fixed error into the retry instruction" {
    python3 -I "$HOST_IO" record-preparation "$REPO" 2026.12.1 2026.11.21 prepared
    for legacy in "encryption key bootstrap failed" "Update process exited with code 1"; do
        python3 -I "$HOST_IO" record-preparation "$REPO" 2026.12.1 2026.11.21 prepared
        status_json "{\"status\": \"failed\", \"from_version\": \"2026.11.21\", \"to_version\": \"2026.12.1\", \"step\": \"ensure_encryption_key\", \"error\": \"$legacy\"}"
        run python3 -I "$HOST_IO" normalize-prepared-failure "$REPO"
        [ "$status" -eq 0 ]
        grep -qF "$PREPARED_TEXT" "$REPO/update-status/current.json"
        grep -q '"status": "failed"' "$REPO/update-status/current.json"
        grep -q '"to_version": "2026.12.1"' "$REPO/update-status/current.json"
        # The record is used once.
        [ ! -e "$REPO/host-state/update-preparation.json" ]
    done
}

@test "the same fixed error without a preparation record is left as it is" {
    status_json '{"status": "failed", "from_version": "2026.11.21", "to_version": "2026.12.1", "error": "encryption key bootstrap failed"}'
    cp "$REPO/update-status/current.json" "$REPO/before.json"
    run python3 -I "$HOST_IO" normalize-prepared-failure "$REPO"
    cmp -s "$REPO/update-status/current.json" "$REPO/before.json"
}

@test "a failed preparation, another target or a real failure is never rewritten" {
    for case in failed-outcome other-target other-error other-source; do
        rm -rf "$REPO/host-state"
        target=2026.12.1; from=2026.11.21; outcome=prepared; error="encryption key bootstrap failed"
        case "$case" in
            failed-outcome) outcome=failed ;;
            other-target)   target=2026.12.2 ;;
            other-error)    error="Database backup failed, so the update was stopped before changing anything." ;;
            other-source)   from=2026.11.20 ;;
        esac
        python3 -I "$HOST_IO" record-preparation "$REPO" "$target" "$from" "$outcome"
        status_json "{\"status\": \"failed\", \"from_version\": \"2026.11.21\", \"to_version\": \"2026.12.1\", \"error\": \"$error\"}"
        cp "$REPO/update-status/current.json" "$REPO/before.json"
        run python3 -I "$HOST_IO" normalize-prepared-failure "$REPO"
        cmp -s "$REPO/update-status/current.json" "$REPO/before.json"
    done
}

@test "the preparation record is private host state and refuses malformed versions" {
    run python3 -I "$HOST_IO" record-preparation "$REPO" 'x; rm -rf /' 2026.11.21 prepared
    [ "$status" -ne 0 ]
    run python3 -I "$HOST_IO" record-preparation "$REPO" 2026.12.1 2026.11.21 maybe
    [ "$status" -ne 0 ]
    python3 -I "$HOST_IO" record-preparation "$REPO" v2026.12.1 2026.11.21 prepared
    [ "$(stat -c %a "$REPO/host-state/update-preparation.json" 2>/dev/null || stat -f %Lp "$REPO/host-state/update-preparation.json")" = 600 ]
    grep -q '"target": "2026.12.1"' "$REPO/host-state/update-preparation.json"
}

@test "read-status-fields reports the published status and step, nothing for an unsafe entry" {
    status_json '{"status": "in_progress", "step": "ensure_encryption_key", "from_version": "2026.11.21", "to_version": "2026.12.1"}'
    run python3 -I "$HOST_IO" read-status-fields "$REPO"
    [ "$output" = "$(printf 'in_progress\tensure_encryption_key\t2026.11.21\t2026.12.1')" ]
    rm "$REPO/update-status/current.json"
    ln -s /etc/passwd "$REPO/update-status/current.json"
    run python3 -I "$HOST_IO" read-status-fields "$REPO"
    [ -z "$output" ]
}

@test "each agent polling round normalizes a prepared legacy failure" {
    python3 -I "$HOST_IO" record-preparation "$REPO" 2026.12.1 2026.11.21 prepared
    status_json '{"status": "failed", "from_version": "2026.11.21", "to_version": "2026.12.1", "error": "Update process exited with code 1"}'
    rm -f "$REPO/update-requests/update_7.json"
    stub_updater 0
    agent_round
    grep -qF "$PREPARED_TEXT" "$REPO/update-status/current.json"
}

# ---------------------------------------------------------------------------
# Fixed read-only SSH recovery diagnostics (plan 03B). The backend may only request the one fixed
# operation; no command, path or username travels in the trigger, and the report has a fixed schema.
# ---------------------------------------------------------------------------

@test "a diagnostics trigger runs only the fixed host operation" {
    printf '{"operation": "ssh-recovery-diagnostics"}' > "$REPO/update-requests/ssh-recovery-diagnostics-1.json"
    rm -f "$REPO/update-requests/update_7.json"
    stub_updater 0
    agent_round
    [ "$(cat "$REPO/updater-calls")" = "ssh-recovery-diagnostics" ]
    [ ! -e "$REPO/update-requests/ssh-recovery-diagnostics-1.json" ]
}

@test "an unknown operation or extra arguments are refused without running anything" {
    for body in '{"operation": "shell"}' '{"operation": "ssh-recovery-diagnostics", "command": "id"}' '{"operation": ["x"]}'; do
        printf '%s' "$body" > "$REPO/update-requests/op.json"
        run python3 -I "$HOST_IO" read-trigger "$REPO" op.json
        [[ "$output" == rejected* ]] || { echo "accepted: $body"; false; }
    done
}

@test "write-diagnostics publishes only the fixed fields with checked types" {
    run python3 -I "$HOST_IO" write-diagnostics "$REPO" instance_id 481516 provider gcp sshd_ok true pubkey_auth true \
        guest_agent true oslogin false eic_package false authorized_keys_command ""
    [ "$status" -eq 0 ]
    python3 -c "import json,sys; d=json.load(open('$REPO/update-status/ssh-recovery-diagnostics.json')); assert d['schema']==1 and d['sshd_ok'] is True and d['oslogin'] is False and d['instance_id']=='481516' and 'generated_at' in d, d"
    run python3 -I "$HOST_IO" write-diagnostics "$REPO" instance_id 481516 private_key secret
    [ "$status" -ne 0 ]
    run python3 -I "$HOST_IO" write-diagnostics "$REPO" instance_id 'x;rm' provider gcp
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# A killed update (review of plan 03A): the updater's EXIT trap cannot run after SIGKILL, so the agent
# (its own service) releases a launch pause, and resumes a frozen backend, once no update holds the lock.
# ---------------------------------------------------------------------------

@test "the agent asks ./bioaf to release a launch pause left by a killed update, before any trigger" {
    stub_updater 0
    mkdir -p "$REPO/update-gate"; echo update > "$REPO/update-gate/work-node-launch.closed"
    agent_round
    [ "$(head -1 "$REPO/updater-calls")" = "release-stale-update-gate" ]
}

@test "with no launch pause the agent never calls the release" {
    stub_updater 0
    agent_round
    absent "release-stale-update-gate" "$REPO/updater-calls"
}

# ---------------------------------------------------------------------------
# Certificate renewal (plan 03.5A): Let's Encrypt IP certificates last about six days, so the agent
# (always running) asks ./bioaf to renew a bioAF-issued certificate every six hours; certbot decides
# whether one is due.
# ---------------------------------------------------------------------------

@test "with a bioAF-issued certificate the agent renews it, then not again within the interval" {
    stub_updater 0
    rm -f "$REPO/update-requests/update_7.json"
    mkdir -p "$REPO/host-state/tls/letsencrypt/config/live/bioaf"
    agent_round
    [ "$(grep -c '^tls-renew$' "$REPO/updater-calls")" -eq 1 ]
    agent_round
    [ "$(grep -c '^tls-renew$' "$REPO/updater-calls")" -eq 1 ]
}

@test "the renewal runs again once the interval has passed" {
    stub_updater 0
    rm -f "$REPO/update-requests/update_7.json"
    mkdir -p "$REPO/host-state/tls/letsencrypt/config/live/bioaf" "$REPO/host-only"
    echo $(( $(date +%s) - 6 * 3600 - 5 )) > "$REPO/host-only/tls-renew.last"
    agent_round
    [ "$(grep -c '^tls-renew$' "$REPO/updater-calls")" -eq 1 ]
}

@test "without a bioAF-issued certificate the agent never renews" {
    stub_updater 0
    agent_round
    absent "tls-renew" "$REPO/updater-calls"
}

@test "the agent installs an uploaded organization certificate only as the fixed operation" {
    stub_updater 0
    printf '{"operation": "tls-install-uploaded"}' > "$REPO/update-requests/tls-install-uploaded-1.json"
    rm -f "$REPO/update-requests/update_7.json"
    agent_round
    [ "$(cat "$REPO/updater-calls")" = "tls-install-uploaded" ]
    [ ! -e "$REPO/update-requests/tls-install-uploaded-1.json" ]
}
