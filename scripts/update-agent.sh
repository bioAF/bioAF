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
# The private log is rotated past this size, keeping two old files (owner-approved budget, issue #110).
AGENT_LOG_MAX_BYTES="${AGENT_LOG_MAX_BYTES:-$((10 * 1024 * 1024))}"
HOST_IO_PY="$(cat "$SCRIPT_DIR/scripts/update_host_io.py" 2>/dev/null || true)"
AGENT_LOG_OPEN=false

# Started as the service (tests source this file for its functions): every trigger read and status write
# runs python3 on the host, so without a usable one the agent stops here, before it creates, opens or
# records anything, and leaves pending triggers and the published status as they are. The reason goes
# to stderr, which the shipped unit sends to the journal.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    # shellcheck source=installer/host_prereqs.sh
    if ! source "$SCRIPT_DIR/installer/host_prereqs.sh" 2>/dev/null || ! declare -F host_python_check >/dev/null; then
        echo "ERROR: installer/host_prereqs.sh is missing from $SCRIPT_DIR, so python3 could not be checked." >&2
        echo "The update agent did not start. Details: sudo journalctl -u bioaf-update-agent" >&2
        exit 1
    fi
    if ! host_python_check; then
        echo "The update agent did not start, so in-app updates will not run. After installing python3:" >&2
        echo "  sudo systemctl restart bioaf-update-agent    (details: sudo journalctl -u bioaf-update-agent)" >&2
        exit 1
    fi
fi

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

_no_foreign_write() {
    # Neither group nor other has write permission on this entry.
    [ -z "$(find "$1" -maxdepth 0 \( -perm -020 -o -perm -002 \) 2>/dev/null)" ]
}

open_agent_log() {
    # Open host-only/agent.log on fd 3, the directory 0700 and the file 0600. The directory is
    # created private. An existing directory or log is used only if it is a real entry (not a link)
    # owned by this user that no one else could have written to; any read or search access others
    # have is then removed (never adding access). Inside a directory only this user can change, the
    # entry cannot be swapped between these checks and the open. Any problem leaves the agent on its
    # inherited stdout/stderr; the old shared update-status/agent.log is never used.
    AGENT_LOG_OPEN=false
    local old_umask
    old_umask=$(umask)
    if [ ! -e "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ]; then
        mkdir -m 700 "$LOG_DIR" 2>/dev/null || true
    fi
    if [ -d "$LOG_DIR" ] && [ ! -L "$LOG_DIR" ] && [ -O "$LOG_DIR" ] && _no_foreign_write "$LOG_DIR" \
        && chmod go-rwx "$LOG_DIR" 2>/dev/null && [ -w "$LOG_DIR" ] && [ -x "$LOG_DIR" ] \
        && { { [ ! -e "$LOG_FILE" ] && [ ! -L "$LOG_FILE" ]; } \
            || { [ -f "$LOG_FILE" ] && [ ! -L "$LOG_FILE" ] && [ -O "$LOG_FILE" ] && _no_foreign_write "$LOG_FILE" \
                && chmod go-rwx "$LOG_FILE" 2>/dev/null; }; } \
        && { umask 077; exec 3>>"$LOG_FILE"; } 2>/dev/null; then
        umask "$old_umask"
        AGENT_LOG_OPEN=true
        return 0
    fi
    umask "$old_umask"
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
    elif [ $exit_code -eq 75 ]; then
        # ./bioaf refused before changing anything: another update, build or setup holds the deployment lock
        # (installer/deployments.sh). Its status is the current one, so it is left alone, and the trigger is
        # kept for the next polling round.
        log "Another bioAF update, build or setup is running; the update to $version will be tried again"
        return 0
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

rotate_agent_log() {
    # Past AGENT_LOG_MAX_BYTES, shift agent.log to agent.log.1 and agent.log.1 to agent.log.2 (the oldest is
    # dropped), then reopen. Only the agent's own private log is touched: open_agent_log already proved the
    # directory is this user's alone, so its entries cannot be swapped underneath.
    [ "$AGENT_LOG_OPEN" = true ] || return 0
    local size
    size=$(wc -c < "$LOG_FILE" 2>/dev/null) || return 0
    [ "$size" -gt "$AGENT_LOG_MAX_BYTES" ] || return 0
    exec 3>&-
    AGENT_LOG_OPEN=false
    [ -f "$LOG_FILE.1" ] && [ ! -L "$LOG_FILE.1" ] && mv -f "$LOG_FILE.1" "$LOG_FILE.2"
    [ -f "$LOG_FILE" ] && [ ! -L "$LOG_FILE" ] && mv -f "$LOG_FILE" "$LOG_FILE.1"
    open_agent_log
}

process_pending() {
    # One polling round: every trigger present now, in name order.
    local trigger_file
    rotate_agent_log
    for trigger_file in "$TRIGGER_DIR"/*.json; do
        # Skip an unmatched glob and plain directories; everything else is judged by the parser.
        [ -e "$trigger_file" ] || [ -L "$trigger_file" ] || continue
        [ -d "$trigger_file" ] && [ ! -L "$trigger_file" ] && continue
        process_trigger "$trigger_file" || true
    done
    # An older updater that ran this release's install hook stopped after the one-time preparation of
    # this agent and recorded its own fixed error; publish the plain retry instruction instead, when the
    # host-owned preparation record matches (scripts/update_host_io.py).
    if [ "$(host_io normalize-prepared-failure 2>/dev/null)" = normalized ]; then
        log "The update helper is prepared; the previous update attempt now asks for a retry."
    fi
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
