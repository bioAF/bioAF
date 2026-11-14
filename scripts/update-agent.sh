#!/usr/bin/env bash
# bioAF Update Agent
#
# A lightweight host-side service that watches for update trigger files
# written by the backend container. When a trigger file appears, the agent
# executes ./bioaf update <version> and writes the result back.
#
# Trigger directory: <repo>/update-requests/   (backend-writable: names and content are untrusted)
# Status directory:  <repo>/update-status/     (backend-writable; the agent publishes current.json)
# Private log:       <repo>/host-only/agent.log when it can be opened safely; otherwise the
#                    service's stdout/stderr (the journal under the shipped systemd unit)
#
# Every read of a trigger and every status write goes through scripts/update_host_io.py, loaded
# into memory at start: no name or payload is ever placed in program text.
#
# Managed by systemd (bioaf-update-agent.service).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRIGGER_DIR="$SCRIPT_DIR/update-requests"
STATUS_DIR="$SCRIPT_DIR/update-status"
LOG_DIR="$SCRIPT_DIR/host-only"
LOG_FILE="$LOG_DIR/agent.log"
POLL_INTERVAL=2
HOST_IO_PY="$(cat "$SCRIPT_DIR/scripts/update_host_io.py" 2>/dev/null || true)"
AGENT_LOG_OPEN=false

mkdir -p "$TRIGGER_DIR"
# 0755 whatever the umask: the backend reads current.json from here, possibly as another UID.
[ -d "$STATUS_DIR" ] || mkdir -m 755 "$STATUS_DIR"

host_io() {
    # host_io <command> [args...]: the in-memory host I/O program, run against this checkout.
    local command="$1"; shift
    python3 -I -c "$HOST_IO_PY" "$command" "$SCRIPT_DIR" "$@"
}

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    [ "$AGENT_LOG_OPEN" = true ] && echo "$msg" >&3
    echo "$msg"
}

_log_dir_safe() {
    # A real directory (not a link) owned by this user and closed to group and other writes.
    [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] && [ -O "$LOG_DIR" ] && [ -w "$LOG_DIR" ] && [ -x "$LOG_DIR" ] \
        && [ -z "$(find "$LOG_DIR" -maxdepth 0 \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]
}

open_agent_log() {
    # Open host-only/agent.log on fd 3. The directory is created private, and an existing one is
    # used only when it is already private to this user, so no other user can swap the entry between
    # the checks below and the open. Any problem leaves the agent on its inherited stdout/stderr;
    # permissions are never relaxed and the old shared update-status/agent.log is never used.
    AGENT_LOG_OPEN=false
    if [ ! -e "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ]; then
        mkdir -m 700 "$LOG_DIR" 2>/dev/null || true
    fi
    if _log_dir_safe \
        && { [ ! -e "$LOG_FILE" ] && [ ! -L "$LOG_FILE" ] || { [ -f "$LOG_FILE" ] && [ ! -L "$LOG_FILE" ] && [ -O "$LOG_FILE" ]; }; } \
        && { local old_umask; old_umask=$(umask); umask 077; exec 3>>"$LOG_FILE"; } 2>/dev/null; then
        umask "$old_umask"
        AGENT_LOG_OPEN=true
        return 0
    fi
    [ -n "${old_umask:-}" ] && umask "$old_umask"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] The private log $LOG_FILE is unavailable or unsafe; logging to the service output instead." >&2
    return 0
}

run_updater() {
    # The updater's output goes to the private log when it is open, otherwise to the service streams.
    if [ "$AGENT_LOG_OPEN" = true ]; then
        BIOAF_AUTO_CONFIRM=1 "$SCRIPT_DIR/bioaf" update "$1" --yes >&3 2>&3 3>&-
    else
        BIOAF_AUTO_CONFIRM=1 "$SCRIPT_DIR/bioaf" update "$1" --yes 3>&-
    fi
}

process_trigger() {
    local trigger_file="$1"
    local name="${trigger_file##*/}"
    local parsed kind version upgrade_id

    # Read and validate from one descriptor opened without following links; the name is passed as an
    # argument, and the result comes back as tab-separated validated fields.
    if ! parsed=$(host_io read-trigger "$name"); then
        log "ERROR: An update trigger could not be read; it was removed without running an update."
        rm -f -- "$TRIGGER_DIR/$name"
        return 1
    fi
    IFS=$'\t' read -r kind version upgrade_id <<< "$parsed"
    if [ "$kind" != ok ]; then
        # The reason is fixed text from the parser; the untrusted name is not logged.
        log "ERROR: Rejected an update trigger (${version:-invalid}); it was removed without running an update."
        rm -f -- "$TRIGGER_DIR/$name"
        return 1
    fi

    log "Processing trigger: $name"
    log "Starting update to version $version (upgrade_id=$upgrade_id)"

    local exit_code=0
    run_updater "$version" || exit_code=$?

    if [ $exit_code -eq 0 ]; then
        log "Update to $version completed successfully"
    else
        log "ERROR: Update to $version failed with exit code $exit_code"

        # Keep the failure the update recorded (its step and reason). Write a generic one only when
        # the update stopped without recording why.
        if [ "$(host_io read-status)" = failed ]; then
            log "Update recorded its own failure in $STATUS_DIR/current.json"
        elif ! host_io write-status status failed to_version "$version" upgrade_id "$upgrade_id" \
            error "Update process exited with code $exit_code" completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; then
            log "ERROR: Could not publish the failure to $STATUS_DIR/current.json"
        fi
    fi

    # Remove the processed trigger
    rm -f -- "$TRIGGER_DIR/$name"
}

process_pending() {
    # One polling round: every trigger present now, in name order.
    local trigger_file
    for trigger_file in "$TRIGGER_DIR"/*.json; do
        # Skip an unmatched glob and plain directories; everything else is judged by the parser.
        [ -e "$trigger_file" ] || [ -L "$trigger_file" ] || continue
        [ -d "$trigger_file" ] && [ ! -L "$trigger_file" ] && continue
        process_trigger "$trigger_file" || true
    done
}

# Sourcing the file (tests) defines the functions without starting the loop.
[ "${BASH_SOURCE[0]}" = "$0" ] || return 0

open_agent_log
# Lets ./bioaf tell whether this running agent is the code on disk (see ensure_update_agent).
host_io record-agent-code || log "WARNING: Could not record the running agent code."
log "Update agent started. Watching $TRIGGER_DIR"

# Main loop: poll for trigger files
while true; do
    process_pending
    sleep "$POLL_INTERVAL"
done
