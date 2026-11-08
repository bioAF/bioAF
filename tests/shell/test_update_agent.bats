#!/usr/bin/env bats
# Tests for scripts/update-agent.sh

AGENT="$BATS_TEST_DIRNAME/../../scripts/update-agent.sh"

setup() {
    REPO="$(mktemp -d)"
    mkdir -p "$REPO/scripts" "$REPO/update-requests" "$REPO/update-status"
    cp "$AGENT" "$REPO/scripts/update-agent.sh"
    printf '{"version": "9.9.9", "upgrade_id": 7}' > "$REPO/update-requests/update_7.json"
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
