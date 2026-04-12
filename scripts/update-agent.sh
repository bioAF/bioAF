#!/usr/bin/env bash
# bioAF Update Agent
#
# A lightweight host-side service that watches for update trigger files
# written by the backend container. When a trigger file appears, the agent
# executes ./bioaf update <version> and writes the result back.
#
# Trigger directory: <repo>/update-requests/
# Status directory:  <repo>/update-status/
#
# Managed by systemd (bioaf-update-agent.service).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TRIGGER_DIR="$SCRIPT_DIR/update-requests"
STATUS_DIR="$SCRIPT_DIR/update-status"
LOG_FILE="$SCRIPT_DIR/update-status/agent.log"
POLL_INTERVAL=2

mkdir -p "$TRIGGER_DIR" "$STATUS_DIR"

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "$msg" >> "$LOG_FILE"
    echo "$msg"
}

process_trigger() {
    local trigger_file="$1"
    local filename
    filename=$(basename "$trigger_file")

    log "Processing trigger: $filename"

    # Read the trigger file
    local version upgrade_id
    version=$(python3 -c "import json; print(json.load(open('$trigger_file'))['version'])" 2>/dev/null)
    upgrade_id=$(python3 -c "import json; print(json.load(open('$trigger_file')).get('upgrade_id', ''))" 2>/dev/null)

    if [ -z "$version" ]; then
        log "ERROR: Could not read version from trigger file"
        rm -f "$trigger_file"
        return 1
    fi

    log "Starting update to version $version (upgrade_id=$upgrade_id)"

    # Run the update with auto-confirm
    local exit_code=0
    BIOAF_AUTO_CONFIRM=1 "$SCRIPT_DIR/bioaf" update "$version" --yes >> "$LOG_FILE" 2>&1 || exit_code=$?

    if [ $exit_code -eq 0 ]; then
        log "Update to $version completed successfully"
    else
        log "ERROR: Update to $version failed with exit code $exit_code"

        # Write failure status so the backend can pick it up on restart
        cat > "$STATUS_DIR/current.json" <<EOF
{"status": "failed", "to_version": "$version", "upgrade_id": "$upgrade_id", "error": "Update process exited with code $exit_code", "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
    fi

    # Remove the processed trigger
    rm -f "$trigger_file"
}

log "Update agent started. Watching $TRIGGER_DIR"

# Main loop: poll for trigger files
while true; do
    for trigger_file in "$TRIGGER_DIR"/*.json; do
        # Skip if no files match the glob
        [ -f "$trigger_file" ] || continue
        process_trigger "$trigger_file"
    done
    sleep "$POLL_INTERVAL"
done
