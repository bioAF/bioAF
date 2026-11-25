#!/usr/bin/env bats
# Tests for the ./bioaf management script

BIOAF_SCRIPT="$BATS_TEST_DIRNAME/../../bioaf"

load network

# No test reaches the host's Docker: retention (installer/deployments.sh) would list, remove and prune real
# images and build cache. This stand-in answers every docker call with a failure, and the space check gets a
# floor of zero so it never depends on the host's free disk.
setup() {
    printf '#!/bin/sh\nexit 1\n' > "$BATS_TEST_TMPDIR/no-docker"
    chmod +x "$BATS_TEST_TMPDIR/no-docker"
    export DEPLOY_DOCKER="$BATS_TEST_TMPDIR/no-docker"
    export BIOAF_NEW_IMAGES_BYTES=0 BIOAF_DISK_FLOOR_MIN_BYTES=0 BIOAF_DISK_FLOOR_PERCENT=0
}

# Stand-in update agents (AGENT_PID) are background processes a test may stop; a failed assertion
# must never leave one stopped, or Bats waits on it forever.
teardown() {
    if [ -n "${AGENT_PID:-}" ]; then
        kill -CONT "$AGENT_PID" 2>/dev/null || true
        pkill -P "$AGENT_PID" 2>/dev/null || true
        kill "$AGENT_PID" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Help / dispatch
# ---------------------------------------------------------------------------

@test "bioaf with no args shows help" {
    run bash "$BIOAF_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "bioaf help lists all management commands" {
    run bash "$BIOAF_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"setup"* ]]
    [[ "$output" == *"start"* ]]
    [[ "$output" == *"stop"* ]]
    [[ "$output" == *"restart"* ]]
    [[ "$output" == *"status"* ]]
    [[ "$output" == *"logs"* ]]
    [[ "$output" == *"migrate"* ]]
    [[ "$output" == *"migrate-down"* ]]
    [[ "$output" == *"backup"* ]]
    [[ "$output" == *"update"* ]]
    [[ "$output" == *"reset-db"* ]]
    [[ "$output" == *"build"* ]]
    [[ "$output" == *"shell"* ]]
    [[ "$output" == *"dbshell"* ]]
    [[ "$output" == *"seed"* ]]
    [[ "$output" == *"register-outputs"* ]]
}

@test "bioaf unknown command exits nonzero" {
    run bash "$BIOAF_SCRIPT" notarealcommand
    [ "$status" -eq 1 ]
    [[ "$output" == *"Unknown command"* ]]
}

@test "bioaf --help shows help" {
    run bash "$BIOAF_SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "bioaf -h shows help" {
    run bash "$BIOAF_SCRIPT" -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
}

# ---------------------------------------------------------------------------
# slugify helper
# ---------------------------------------------------------------------------

@test "slugify converts uppercase to lowercase" {
    result=$(bash -c "
        slugify() {
            echo \"\$1\" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-\$//'
        }
        slugify 'My Organization'
    ")
    [ "$result" = "my-organization" ]
}

@test "slugify strips special characters" {
    result=$(bash -c "
        slugify() {
            echo \"\$1\" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-\$//'
        }
        slugify 'Acme Corp. #1!'
    ")
    [ "$result" = "acme-corp-1" ]
}

@test "slugify collapses multiple hyphens" {
    result=$(bash -c "
        slugify() {
            echo \"\$1\" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//;s/-\$//'
        }
        slugify 'foo---bar'
    ")
    [ "$result" = "foo-bar" ]
}

# ---------------------------------------------------------------------------
# parse_version_from_image
# ---------------------------------------------------------------------------

@test "parse_version_from_image strips ghcr prefix and v" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && parse_version_from_image 'ghcr.io/bioaf/bioaf-backend:v0.11.5'")
    [ "$result" = "0.11.5" ]
}

@test "parse_version_from_image accepts tag without v prefix" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && parse_version_from_image 'ghcr.io/bioaf/bioaf-backend:0.11.5'")
    [ "$result" = "0.11.5" ]
}

@test "parse_version_from_image rejects 'latest' tag" {
    run bash -c "source '$BIOAF_SCRIPT' && parse_version_from_image 'ghcr.io/bioaf/bioaf-backend:latest'"
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

@test "parse_version_from_image rejects image with no tag" {
    run bash -c "source '$BIOAF_SCRIPT' && parse_version_from_image 'ghcr.io/bioaf/bioaf-backend'"
    [ "$status" -ne 0 ]
    [ -z "$output" ]
}

@test "parse_version_from_image rejects empty input" {
    run bash -c "source '$BIOAF_SCRIPT' && parse_version_from_image ''"
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# ghcr_reachable  (the connectivity guard for the network tests below)
# ---------------------------------------------------------------------------

# Put a fake curl first on PATH that prints the given HTTP code, as curl's
# -w '%{http_code}' does, and exits with the given status.
fake_curl() {
    mkdir -p "$BATS_TEST_TMPDIR/bin"
    printf '#!/bin/sh\nprintf %%s %s\nexit %s\n' "$1" "$2" > "$BATS_TEST_TMPDIR/bin/curl"
    chmod +x "$BATS_TEST_TMPDIR/bin/curl"
    PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

@test "ghcr_reachable is false when curl gets no response (HTTP 000)" {
    fake_curl 000 28
    run ghcr_reachable
    [ "$status" -ne 0 ]
}

@test "ghcr_reachable is false when curl prints nothing" {
    fake_curl '' 6
    run ghcr_reachable
    [ "$status" -ne 0 ]
}

@test "ghcr_reachable is true when ghcr.io answers, even with an error status" {
    fake_curl 401 22
    run ghcr_reachable
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# verify_image_available  (uses real ghcr.io; skipped if no network)
# ---------------------------------------------------------------------------

@test "verify_image_available returns 0 for known-published tag" {
    if ! ghcr_reachable; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_image_available 'bioaf/bioaf-backend' 'v0.11.5'"
    [ "$status" -eq 0 ]
}

@test "verify_image_available returns 1 for missing tag" {
    if ! ghcr_reachable; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_image_available 'bioaf/bioaf-backend' 'v9999.99.99'"
    [ "$status" -eq 1 ]
}

# ---------------------------------------------------------------------------
# verify_release_images
# ---------------------------------------------------------------------------

@test "verify_release_images reports friendly retry message when images missing" {
    if ! ghcr_reachable; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_release_images '9999.99.99'"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not yet published"* ]]
    [[ "$output" == *"try again in a few minutes"* ]]
}

# ---------------------------------------------------------------------------
# pin_image_tag / ensure_pinned_image_tag
# ---------------------------------------------------------------------------

@test "pin_image_tag appends BIOAF_IMAGE_TAG when missing" {
    local env_file
    env_file="$(mktemp)"
    printf 'POSTGRES_USER=bioaf\nSECRET_KEY=x\n' > "$env_file"
    BIOAF_ENV_FILE="$env_file" run bash -c "source '$BIOAF_SCRIPT' && pin_image_tag 'v1.2.3'"
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_IMAGE_TAG=v1.2.3$' "$env_file"
    # Other lines preserved.
    grep -q '^POSTGRES_USER=bioaf$' "$env_file"
    grep -q '^SECRET_KEY=x$' "$env_file"
    rm -f "$env_file"
}

@test "pin_image_tag replaces existing BIOAF_IMAGE_TAG line in place" {
    local env_file
    env_file="$(mktemp)"
    printf 'POSTGRES_USER=bioaf\nBIOAF_IMAGE_TAG=v1.0.0\nSECRET_KEY=x\n' > "$env_file"
    BIOAF_ENV_FILE="$env_file" run bash -c "source '$BIOAF_SCRIPT' && pin_image_tag 'v9.9.9'"
    [ "$status" -eq 0 ]
    # Exactly one BIOAF_IMAGE_TAG line, with the new value.
    [ "$(grep -c '^BIOAF_IMAGE_TAG=' "$env_file")" -eq 1 ]
    grep -q '^BIOAF_IMAGE_TAG=v9.9.9$' "$env_file"
    # Sibling lines preserved.
    grep -q '^POSTGRES_USER=bioaf$' "$env_file"
    grep -q '^SECRET_KEY=x$' "$env_file"
    rm -f "$env_file"
}

@test "pin_image_tag is idempotent" {
    local env_file
    env_file="$(mktemp)"
    : > "$env_file"
    BIOAF_ENV_FILE="$env_file" bash -c "source '$BIOAF_SCRIPT' && pin_image_tag 'v0.11.9'"
    BIOAF_ENV_FILE="$env_file" bash -c "source '$BIOAF_SCRIPT' && pin_image_tag 'v0.11.9'"
    [ "$(grep -c '^BIOAF_IMAGE_TAG=' "$env_file")" -eq 1 ]
    grep -q '^BIOAF_IMAGE_TAG=v0.11.9$' "$env_file"
    rm -f "$env_file"
}

@test "pin_image_tag fails with empty tag argument" {
    local env_file
    env_file="$(mktemp)"
    BIOAF_ENV_FILE="$env_file" run bash -c "source '$BIOAF_SCRIPT' && pin_image_tag ''"
    [ "$status" -ne 0 ]
    rm -f "$env_file"
}

@test "ensure_pinned_image_tag is a no-op when env already has BIOAF_IMAGE_TAG" {
    local env_file
    env_file="$(mktemp)"
    printf 'BIOAF_IMAGE_TAG=v0.5.0\n' > "$env_file"
    # If this called get_running_version, it would invoke `docker compose ps`
    # against a non-existent compose file and fail. Use a missing compose file
    # to prove ensure_pinned_image_tag short-circuits without touching docker.
    BIOAF_ENV_FILE="$env_file" BIOAF_COMPOSE_FILE="/nonexistent/compose.yml" \
        run bash -c "source '$BIOAF_SCRIPT' && ensure_pinned_image_tag"
    [ "$status" -eq 0 ]
    [ "$(grep -c '^BIOAF_IMAGE_TAG=' "$env_file")" -eq 1 ]
    grep -q '^BIOAF_IMAGE_TAG=v0.5.0$' "$env_file"
    rm -f "$env_file"
}

@test "ensure_pinned_image_tag falls back to disk version when no pin and no container" {
    local env_file
    env_file="$(mktemp)"
    : > "$env_file"
    # No backend container exists in this test environment -- get_running_version
    # will fall back to get_current_version (reads backend/pyproject.toml).
    BIOAF_ENV_FILE="$env_file" run bash -c "source '$BIOAF_SCRIPT' && ensure_pinned_image_tag"
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_IMAGE_TAG=v[0-9]' "$env_file"
    rm -f "$env_file"
}

# ---------------------------------------------------------------------------
# build_setup_reexec_cmd
#
# When setup re-execs under `sg docker` to activate docker-group membership,
# EVERY original flag must survive the hop. A previous version preserved only
# --version, silently dropping --prefill / --local-build / --verbose, so users
# whose shell lacked the docker group got a bare `./bioaf setup`.
# ---------------------------------------------------------------------------

@test "build_setup_reexec_cmd preserves --prefill and its path across re-exec" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && build_setup_reexec_cmd '/opt/bioaf/bioaf' --prefill /home/u/.bioaf-prefill.yaml")
    eval "set -- $result"
    [ "$1" = "/opt/bioaf/bioaf" ]
    [ "$2" = "setup" ]
    [ "$3" = "--prefill" ]
    [ "$4" = "/home/u/.bioaf-prefill.yaml" ]
}

@test "build_setup_reexec_cmd preserves --local-build alongside --prefill" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && build_setup_reexec_cmd '/opt/bioaf/bioaf' --local-build --prefill /tmp/p.yaml")
    eval "set -- $result"
    [ "$2" = "setup" ]
    [[ "$result" == *"--local-build"* ]]
    [[ "$result" == *"--prefill"* ]]
    [[ "$result" == *"/tmp/p.yaml"* ]]
}

@test "build_setup_reexec_cmd preserves --version (no regression)" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && build_setup_reexec_cmd '/opt/bioaf/bioaf' --version 0.8.1")
    eval "set -- $result"
    [ "$3" = "--version" ]
    [ "$4" = "0.8.1" ]
}

@test "build_setup_reexec_cmd quotes paths with spaces so they round-trip as one arg" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && build_setup_reexec_cmd '/opt/bioaf/bioaf' --prefill '/tmp/my dir/p.yaml'")
    eval "set -- $result"
    [ "$4" = "/tmp/my dir/p.yaml" ]
}

@test "build_setup_reexec_cmd with no extra args yields bare 'setup'" {
    result=$(bash -c "source '$BIOAF_SCRIPT' && build_setup_reexec_cmd '/opt/bioaf/bioaf'")
    eval "set -- $result"
    [ "$1" = "/opt/bioaf/bioaf" ]
    [ "$2" = "setup" ]
    [ "$#" -eq 2 ]
}

# ---------------------------------------------------------------------------
# discover_prefill_file
#
# --local-build pre-applies the prefill install-gcp.sh already dropped on the
# box, so `./bioaf setup --local-build` fills platform_config without an
# explicit --prefill.
# ---------------------------------------------------------------------------

@test "discover_prefill_file finds the install-gcp.sh handoff prefill in HOME" {
    local tmp
    tmp="$(mktemp -d)"
    : > "$tmp/.bioaf-prefill.yaml"
    result=$(HOME="$tmp" bash -c "source '$BIOAF_SCRIPT' && discover_prefill_file")
    [ "$result" = "$tmp/.bioaf-prefill.yaml" ]
    rm -rf "$tmp"
}

@test "discover_prefill_file falls back to ~/.bioaf/prefill.yaml" {
    local tmp
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/.bioaf"
    : > "$tmp/.bioaf/prefill.yaml"
    result=$(HOME="$tmp" bash -c "source '$BIOAF_SCRIPT' && discover_prefill_file")
    [ "$result" = "$tmp/.bioaf/prefill.yaml" ]
    rm -rf "$tmp"
}

@test "discover_prefill_file prefers the handoff path when both exist" {
    local tmp
    tmp="$(mktemp -d)"
    : > "$tmp/.bioaf-prefill.yaml"
    mkdir -p "$tmp/.bioaf"
    : > "$tmp/.bioaf/prefill.yaml"
    result=$(HOME="$tmp" bash -c "source '$BIOAF_SCRIPT' && discover_prefill_file")
    [ "$result" = "$tmp/.bioaf-prefill.yaml" ]
    rm -rf "$tmp"
}

@test "discover_prefill_file outputs nothing when no prefill present" {
    local tmp
    tmp="$(mktemp -d)"
    result=$(HOME="$tmp" bash -c "source '$BIOAF_SCRIPT' && discover_prefill_file")
    [ -z "$result" ]
    rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# Database upgrade (plan 9): backups that stop on failure, settled-state checks
# ---------------------------------------------------------------------------

# A stand-in for `docker compose ...`: the test sets FAKE_DUMP / FAKE_DUMP_STATUS / FAKE_SCHEMA_STATUS /
# FAKE_READY and the stub answers the commands the script runs.
#
# Plan 21 additions (all optional; unset keeps the earlier behavior):
#   FAKE_CHECK_STATUS      exit of the one-off settings check (`run ... app.cli.check_settings`)
#   FAKE_PULL_STATUS       exit of `pull`;  FAKE_UP_STATUS  exit of `up -d`
#   FAKE_STATUS_EXEC_FAILS=1  `exec ... schema_upgrade status` fails (a restarting backend)
#   FAKE_STATE=<dir>       service state: `up` writes the effective tag to <dir>/backend and
#                          <dir>/frontend and marks the corrected env applied (<dir>/applied)
#   FAKE_UPGRADE_NEEDS_UP=1   the upgrade `exec` fails until an `up` has applied the env
# The check, pull and up record their arguments and the effective tag (shell over env file) in
# FAKE_LOG, and the check also records the persisted pin at the time it ran.
make_fake_dc() {
    local dir="$1"
    cat > "$dir/fake-dc" <<'STUB'
#!/usr/bin/env bash
args="$*"
log="${FAKE_LOG:-/dev/null}"
env_file=""
prev=""
for a in "$@"; do
    [ "$prev" = "--env-file" ] && env_file="$a"
    prev="$a"
done
pinned() { [ -n "$env_file" ] && grep '^BIOAF_IMAGE_TAG=' "$env_file" 2>/dev/null | tail -1 | cut -d= -f2-; }
effective_tag() { if [ -n "${BIOAF_IMAGE_TAG+x}" ]; then echo "$BIOAF_IMAGE_TAG"; else pinned; fi; }
case "$args" in
    *"pg_dump"*) printf '%s' "${FAKE_DUMP-}"; exit "${FAKE_DUMP_STATUS:-0}" ;;
    *"app.schema_upgrade status"*)
        [ "${FAKE_STATUS_EXEC_FAILS:-0}" = 1 ] && exit 1
        printf '%s' "${FAKE_SCHEMA_STATUS:-{\}}"; exit 0 ;;
    *"app.schema_upgrade run"*)
        if [ "${FAKE_UPGRADE_NEEDS_UP:-0}" = 1 ] && [ ! -e "${FAKE_STATE:-/nonexistent}/applied" ]; then
            echo "schema_upgrade exec refused (restarting)" >> "$log"; exit 1
        fi
        echo "schema_upgrade run" >> "$log"; exit "${FAKE_RUN_STATUS:-0}" ;;
    *"app.cli.check_work_node_readiness"*)
        # FAKE_READINESS: space-separated exit statuses, one per call (default 0); the marker state is logged.
        n=0; [ -f "${FAKE_STATE:-/nonexistent}/readiness-calls" ] && n=$(cat "$FAKE_STATE/readiness-calls")
        [ -n "${FAKE_STATE:-}" ] && echo $((n + 1)) > "$FAKE_STATE/readiness-calls"
        set -- ${FAKE_READINESS:-0}; shift "$n" 2>/dev/null || set --
        marker=no; [ -e "${FAKE_GATE_MARKER:-/nonexistent}" ] && marker=yes
        echo "readiness tag=$(effective_tag) pin=$(pinned) marker=$marker" >> "$log"
        exit "${1:-0}" ;;
    *"app.cli.check_settings"*)
        echo "check args=[$args] tag=$(effective_tag) pin=$(pinned)" >> "$log"
        exit "${FAKE_CHECK_STATUS:-0}" ;;
    *" pull "*|*" pull")
        echo "pull tag=$(effective_tag)" >> "$log"; exit "${FAKE_PULL_STATUS:-0}" ;;
    *" up "*|*" up")
        echo "up args=[$args] tag=$(effective_tag)" >> "$log"
        [ "${FAKE_UP_STATUS:-0}" = 0 ] || exit "$FAKE_UP_STATUS"
        if [ -n "${FAKE_STATE:-}" ]; then
            effective_tag > "$FAKE_STATE/backend"; effective_tag > "$FAKE_STATE/frontend"; : > "$FAKE_STATE/applied"
        fi
        exit 0 ;;
    *"health/ready"*) [ "${FAKE_READY:-yes}" = yes ] && exit 0 || exit 1 ;;
    *"app.cli.reset_install_metrics_identity"*)
        echo "reset_install_metrics_identity args=[$args]" >> "$log"; exit 0 ;;
    *"app.cli.record_upgrade_metric"*)
        echo "record_upgrade_metric args=[$args]" >> "$log"; exit "${FAKE_RECORD_STATUS:-0}" ;;
    *"app.cli.record_infrastructure_notice"*)
        echo "record_infrastructure_notice args=[$args]" >> "$log"; exit "${FAKE_NOTICE_STATUS:-0}" ;;
esac
exit 0
STUB
    chmod +x "$dir/fake-dc"
}

@test "backup_database keeps a verified gzipped dump" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_DUMP="-- dump" bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; backup_database '$tmp/out.sql.gz'"
    [ "$status" -eq 0 ]
    gzip -t "$tmp/out.sql.gz"
    [ "$(gunzip -c "$tmp/out.sql.gz")" = "-- dump" ]
}

@test "backup_database writes a dump only its owner can read, in a private directory" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_DUMP="-- dump" bash -c "umask 022; source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; backup_database '$tmp/backups/out.sql.gz'"
    [ "$status" -eq 0 ]
    [ "$(stat -c %a "$tmp/backups/out.sql.gz" 2>/dev/null || stat -f %Lp "$tmp/backups/out.sql.gz")" = "600" ]
    [ "$(stat -c %a "$tmp/backups" 2>/dev/null || stat -f %Lp "$tmp/backups")" = "700" ]
}

@test "ensure_backups_dir makes backups/ private and tightens earlier bioAF backups only" {
    tmp="$(mktemp -d)"; mkdir -p "$tmp/backups"; chmod 755 "$tmp/backups"
    touch "$tmp/backups/bioaf_pre_update_1_to_2.sql.gz" "$tmp/backups/bioaf_pre_upgrade_131_to_132_x.dump" "$tmp/backups/notes.txt"
    chmod 644 "$tmp/backups/"*
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'; ensure_backups_dir"
    [ "$status" -eq 0 ]
    mode() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
    [ "$(mode "$tmp/backups")" = "700" ]
    [ "$(mode "$tmp/backups/bioaf_pre_update_1_to_2.sql.gz")" = "600" ]
    [ "$(mode "$tmp/backups/bioaf_pre_upgrade_131_to_132_x.dump")" = "600" ]
    [ "$(mode "$tmp/backups/notes.txt")" = "644" ]
}

@test "backup_database fails and removes the file when pg_dump fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_DUMP="partial" FAKE_DUMP_STATUS=1 bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; backup_database '$tmp/out.sql.gz'"
    [ "$status" -ne 0 ]
    [ ! -e "$tmp/out.sql.gz" ]
}

@test "backup_database fails on an empty dump even when pg_dump exits 0" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_DUMP="" bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; backup_database '$tmp/out.sql.gz'"
    [ "$status" -ne 0 ]
    [ ! -e "$tmp/out.sql.gz" ]
}

@test "db_upgrade_settled trusts only the running image's own verdict" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_SCHEMA_STATUS='{"settled": true, "code_head": "132", "record": {"status": "completed"}}' \
        bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; db_upgrade_settled"
    [ "$status" -eq 0 ]
    run env FAKE_SCHEMA_STATUS='{"settled": false, "code_head": "132", "record": {"status": "completed"}}' \
        bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; db_upgrade_settled"
    [ "$status" -ne 0 ]
    run env FAKE_SCHEMA_STATUS='{}' bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; db_upgrade_settled"
    [ "$status" -ne 0 ]
}

@test "update retries the database upgrade when the target image is already running" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_SCHEMA_STATUS='{"settled": false}' \
        BIOAF_AUTO_CONFIRM=1 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc'; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_running_version() { echo 9.9.9; }
        wait_for_db() { :; }
        backup_database() { echo backup >> '$tmp/log'; }
        cmd_update 9.9.9 --yes"
    [ "$status" -eq 0 ]
    grep -q backup "$tmp/log"
    grep -q "schema_upgrade run" "$tmp/log"
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
}

@test "update stops when the pre-update backup fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_SCHEMA_STATUS='{"settled": false}' BIOAF_AUTO_CONFIRM=1 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc'; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_running_version() { echo 9.9.9; }
        wait_for_db() { :; }
        backup_database() { return 1; }
        cmd_update 9.9.9 --yes"
    [ "$status" -ne 0 ]
    ! grep -q "schema_upgrade run" "$tmp/log" 2>/dev/null
    grep -q '"status": "failed"' "$tmp/update-status/current.json"
    grep -q '"step": "backup"' "$tmp/update-status/current.json"
}

@test "update records failure when the database upgrade fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_RUN_STATUS=1 FAKE_SCHEMA_STATUS='{"settled": false}' BIOAF_AUTO_CONFIRM=1 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc'; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_running_version() { echo 9.9.9; }
        wait_for_db() { :; }
        backup_database() { :; }
        cmd_update 9.9.9 --yes"
    [ "$status" -ne 0 ]
    grep -q '"status": "failed"' "$tmp/update-status/current.json"
    grep -q '"step": "migrate"' "$tmp/update-status/current.json"
}

@test "update is a no-op only when the running version's database upgrade is settled" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_SCHEMA_STATUS='{"settled": true}' bash -c "
        source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc'; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_running_version() { echo 9.9.9; }
        cmd_update 9.9.9 --yes"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Nothing to update"* ]]
    [ ! -e "$tmp/log" ]
}

@test "migrate runs the shared database upgrade lifecycle" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; wait_for_db() { :; }; cmd_migrate"
    [ "$status" -eq 0 ]
    grep -q "schema_upgrade run" "$tmp/log"
}

@test "recovery dumps in backups/ are never offered to git" {
    # A dump holds the whole database; the checkout on the host must not list it as a file to commit.
    run git -C "$BATS_TEST_DIRNAME/../.." check-ignore -q backups/bioaf_pre_upgrade_131_to_132_x.dump
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Settings preflight for update and migrate (plan 21)
#
# Shell-state tests: the stub proves host ordering, status and pinning. It does not prove Docker's
# exit-code propagation or Compose's recreation of changed containers.
# ---------------------------------------------------------------------------

# A sourced ./bioaf with every host side effect outside Compose stubbed. get_running_version reports
# the stub's backend image; FAKE_STATE starts with both services on $1 (e.g. v1.0.0).
update_env() {
    local tmp="$1" start_tag="$2"
    mkdir -p "$tmp/state"
    echo "$start_tag" > "$tmp/state/backend"; echo "$start_tag" > "$tmp/state/frontend"
    printf '#!/bin/sh\nexit 0\n' > "$tmp/install.sh"
    cat > "$tmp/harness.sh" <<HARNESS
source '$BIOAF_SCRIPT' >/dev/null
DC='$tmp/fake-dc --env-file $tmp/.env'; DC_PULL='$tmp/fake-dc --env-file $tmp/.env pull'
SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
get_running_version() { local t; t=\$(cat '$tmp/state/backend'); echo "\${t#v}"; }
verify_release_images() { return 0; }
git() { echo "git \$*" >> '$tmp/log'; }
wait_for_db() { :; }
ensure_cloud_logging() { :; }
backup_database() { echo backup >> '$tmp/log'; }
HARNESS
}

@test "update pulls and checks the target under a scoped tag, then pins and restarts both services" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'OTHER=x\nBIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    grep -q '^pull tag=v2.0.0$' "$tmp/log"
    # The check ran on the target image while the env file still pinned the old release.
    grep -q '^check args=\[.*run --rm --no-deps -T backend python -m app.cli.check_settings\] tag=v2.0.0 pin=v1.0.0$' "$tmp/log"
    grep -q '^BIOAF_IMAGE_TAG=v2.0.0$' "$tmp/.env"
    grep -q '^OTHER=x$' "$tmp/.env"
    # Owner-approved change (2026-11-24, plan 03A): the Work Node readiness check runs on the target image,
    # nginx alone is recreated to close launch admission, and the check runs again before the pin.
    [ "$(grep -E '^(pull|check|readiness|up|schema_upgrade run|backup)' "$tmp/log" | cut -d' ' -f1)" = \
      "$(printf 'backup\npull\ncheck\nreadiness\nup\nreadiness\nup\nschema_upgrade')" ]
    grep -q '^up args=\[.*up -d --no-deps nginx\] tag=v1.0.0$' "$tmp/log"
    [ "$(grep -c '^readiness tag=v2.0.0 pin=v1.0.0' "$tmp/log")" -eq 2 ]
    [ "$(cat "$tmp/state/backend")" = v2.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v2.0.0 ]
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
}

# ---------------------------------------------------------------------------
# Work Node first-update gate (plan 03A). The readiness check runs from the target image before the pin;
# launch POSTs are closed at nginx (update-gate/work-node-launch.closed) while accepted launches drain,
# then the check runs again. The updater never stops a node to pass.
# ---------------------------------------------------------------------------

gate_run() {
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_GATE_MARKER="$tmp/update-gate/work-node-launch.closed" \
        BIOAF_SKIP_RESTART_WARN=1 BIOAF_LAUNCH_DRAIN_SECONDS=0 "$@" bash -c "source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
}

@test "older work nodes defer the update with the old services, pin and open launches" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    gate_run FAKE_READINESS=3
    [ "$status" -ne 0 ]
    grep -q '"step": "work_node_readiness"' "$tmp/update-status/current.json"
    grep -qF 'Some older work nodes are still running. Finish saving your work and stop them when convenient, then retry the update. bioAF will leave them running.' "$tmp/update-status/current.json"
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
    [ "$(cat "$tmp/state/backend")" = v1.0.0 ]
    [ ! -e "$tmp/update-gate/work-node-launch.closed" ]
}

@test "a readiness check that cannot run never lets the update proceed" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    gate_run FAKE_READINESS=1
    [ "$status" -ne 0 ]
    grep -qF 'bioAF could not check whether this update is safe. Your current installation is still running. Try again shortly.' "$tmp/update-status/current.json"
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
}

@test "launches are closed during the final check and reopened when a late launch defers the update" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    gate_run FAKE_READINESS="0 3"
    [ "$status" -ne 0 ]
    [ "$(grep '^readiness' "$tmp/log" | sed 's/.*marker=//')" = "$(printf 'no\nyes')" ]
    [ ! -e "$tmp/update-gate/work-node-launch.closed" ]
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
    # Only nginx was recreated; the backend that holds older nodes' keys kept running.
    [ "$(grep -c '^up ' "$tmp/log")" -eq 1 ]
    grep -q '^up args=\[.*--no-deps nginx\]' "$tmp/log"
    [ "$(cat "$tmp/state/backend")" = v1.0.0 ]
}

@test "a successful update reopens launches once the new backend is ready" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    gate_run
    [ "$status" -eq 0 ]
    [ ! -e "$tmp/update-gate/work-node-launch.closed" ]
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
}

@test "a marker left by an interrupted update is cleared by the next update" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    mkdir -p "$tmp/update-gate"; echo stale > "$tmp/update-gate/work-node-launch.closed"
    gate_run
    [ "$status" -eq 0 ]
    [ "$(grep -m1 '^readiness' "$tmp/log" | sed 's/.*marker=//')" = no ]
}

@test "a retry with the target already running does not rerun the Work Node gate" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    ! grep -q '^readiness' "$tmp/log"
}

@test "nginx refuses Work Node launch POSTs while the update gate marker exists" {
    conf="$BATS_TEST_DIRNAME/../../docker/nginx.conf"
    compose="$BATS_TEST_DIRNAME/../../docker/docker-compose.yml"
    grep -q 'location = /api/v1/work-nodes/sessions' "$conf"
    grep -q -- '-f /etc/nginx/update-gate/work-node-launch.closed' "$conf"
    grep -q 'request_method = POST' "$conf"
    grep -q '\.\./update-gate:/etc/nginx/update-gate:ro' "$compose"
}

@test "update stops at a rejected settings check with the old pin, both old services and the caller's tag" {
    for start in pinned absent exported; do
        tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
        case "$start" in
            pinned)   printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"; caller="env -u BIOAF_IMAGE_TAG" ;;
            absent)   printf 'OTHER=x\n' > "$tmp/.env"; caller="env -u BIOAF_IMAGE_TAG" ;;
            exported) printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"; caller="env BIOAF_IMAGE_TAG=v0.9.9" ;;
        esac
        cp "$tmp/.env" "$tmp/env.before"
        run $caller FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_CHECK_STATUS=78 BIOAF_SKIP_RESTART_WARN=1 bash -c "
            source '$tmp/harness.sh'; rc=0; ( cmd_update 2.0.0 --yes ) || rc=\$?; echo \"rc=\$rc caller=\${BIOAF_IMAGE_TAG-unset}\""
        [[ "$output" == *"rc=1"* ]]
        if [ "$start" = exported ]; then [[ "$output" == *"caller=v0.9.9"* ]]; else [[ "$output" == *"caller=unset"* ]]; fi
        # The target was what got checked, whatever the shell exported.
        grep -q '^check .* tag=v2.0.0 ' "$tmp/log"
        cmp -s "$tmp/.env" "$tmp/env.before"
        [ "$(cat "$tmp/state/backend")" = v1.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v1.0.0 ]
        ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
        grep -q '"status": "failed"' "$tmp/update-status/current.json"
        grep -q '"step": "settings_preflight"' "$tmp/update-status/current.json"
        grep -q 'BIOAF_COMPUTE_MODE' "$tmp/update-status/current.json"
        grep -q './bioaf update 2.0.0' "$tmp/update-status/current.json"
        ! grep -qi 'migrat\|database upgrade' "$tmp/update-status/current.json"
    done
}

@test "update reports a settings check that could not run without claiming the settings are invalid" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_CHECK_STATUS=1 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    grep -q '"step": "settings_preflight"' "$tmp/update-status/current.json"
    grep -q 'could not be checked' "$tmp/update-status/current.json"
    ! grep -q 'rejected' "$tmp/update-status/current.json"
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
}

@test "a rejected update, corrected and rerun, brings both services and the pin to the target" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_CHECK_STATUS=78 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    [ "$(cat "$tmp/state/backend")" = v1.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v1.0.0 ]
    # The operator corrects the mode and reruns the full update.
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    [ "$(cat "$tmp/state/backend")" = v2.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v2.0.0 ]
    grep -q '^BIOAF_IMAGE_TAG=v2.0.0$' "$tmp/.env"
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
}

@test "update recovers a target backend an older updater left restarting with a bad mode" {
    # The older updater pinned v2.0.0 and started it; the backend rejects its mode and restarts, so
    # its status exec fails. After the operator corrects docker/.env, the new updater must apply
    # the env with up before the upgrade exec, which the restarting container cannot serve.
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 \
        FAKE_UPGRADE_NEEDS_UP=1 bash -c "source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    [ "$(grep -E '^(check|backup|up|schema_upgrade)' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'check\nbackup\nup\nschema_upgrade')" ]
    ! grep -q 'exec refused' "$tmp/log"
    ! grep -q -- '--force-recreate' "$tmp/log"
    ! grep -q '^pull\|^git ' "$tmp/log"
    [ "$(cat "$tmp/state/backend")" = v2.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v2.0.0 ]
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
    [[ "$output" != *"database upgrade did not complete"* ]]
}

@test "the unsettled retry does not reconcile services when its backup fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 bash -c "
        source '$tmp/harness.sh'; backup_database() { return 1; }; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    grep -q '"step": "backup"' "$tmp/update-status/current.json"
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
}

@test "the unsettled retry records a failed restart and does not migrate when up fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 FAKE_UP_STATUS=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    grep -q '"status": "failed"' "$tmp/update-status/current.json"
    grep -q '"step": "restart"' "$tmp/update-status/current.json"
    ! grep -q '^schema_upgrade' "$tmp/log"
}

@test "the unsettled retry stops at a rejected check before the backup" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 FAKE_CHECK_STATUS=78 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    grep -q '"step": "settings_preflight"' "$tmp/update-status/current.json"
    ! grep -qE '^(backup|up|schema_upgrade)' "$tmp/log"
}

@test "migrate stops at rejected settings before waiting for the database or upgrading" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_CHECK_STATUS=78 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; SCRIPT_DIR='$tmp'
        wait_for_db() { echo wait_for_db >> '$tmp/log'; }; cmd_migrate"
    [ "$status" -ne 0 ]
    grep -q '^check ' "$tmp/log"
    ! grep -qE '^(wait_for_db|schema_upgrade)' "$tmp/log"
    [[ "$output" == *"BIOAF_COMPUTE_MODE"* ]]
    [[ "$output" == *"up -d backend"* ]]
    [[ "$output" != *"Database upgrade failed"* ]]
    [ ! -e "$tmp/update-status/current.json" ]
}

@test "migrate reports a settings check that could not run, without claiming invalid settings" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_CHECK_STATUS=1 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; wait_for_db() { :; }; cmd_migrate"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not be checked"* ]]
    [[ "$output" != *"rejected"* ]]
    ! grep -q '^schema_upgrade' "$tmp/log"
}

@test "migrate with valid settings still reports a genuine upgrade failure as one" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_RUN_STATUS=1 bash -c "
        source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; wait_for_db() { :; }; cmd_migrate"
    [ "$status" -ne 0 ]
    [ "$(cut -d' ' -f1 "$tmp/log")" = "$(printf 'check\nschema_upgrade')" ]
    [[ "$output" == *"Database upgrade failed"* ]]
}

@test "update with valid settings still records a genuine upgrade failure at migrate" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_RUN_STATUS=1 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    grep -q '"step": "migrate"' "$tmp/update-status/current.json"
}

# ---------------------------------------------------------------------------
# Ops Agent config reconciliation (plan 22). Shell stubs only: the engine, systemctl and sudo are
# fakes, so these prove outcomes and ordering, not that the real agent accepts the parser.
# ---------------------------------------------------------------------------

LEGACY_OPS_CONFIG="$BATS_TEST_DIRNAME/fixtures/ops-agent-config.v0.6.3.yaml"

# cloud_env <tmp>: fake sudo/systemctl/engine on PATH; the agent config lives under <tmp>/etc.
cloud_env() {
    local tmp="$1"
    mkdir -p "$tmp/bin" "$tmp/etc"
    # FAKE_SUDO_FAIL names one privileged command that fails (e.g. cat: an unreadable config).
    printf '#!/bin/sh\n[ -n "$FAKE_SUDO_FAIL" ] && [ "$1" = "$FAKE_SUDO_FAIL" ] && exit 2\n"$@"\n' > "$tmp/bin/sudo"
    # docker info reports Docker's root directory, where container logs live.
    printf '#!/bin/sh\n[ "$1" = info ] || exit 1\n[ "${FAKE_DOCKER_STATUS:-0}" = 0 ] || exit "$FAKE_DOCKER_STATUS"\necho "${FAKE_DOCKER_ROOT:-/var/lib/docker}"\n' > "$tmp/bin/docker"
    # Never fetch or run the real agent installer from a test.
    printf '#!/bin/sh\necho "curl $*" >> "$FAKE_LOG"\nexit 1\n' > "$tmp/bin/curl"
    cat > "$tmp/bin/systemctl" <<'STUB'
#!/bin/sh
echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
    restart) exit "${FAKE_RESTART_STATUS:-0}" ;;
    is-active) exit "${FAKE_ACTIVE_STATUS:-0}" ;;
esac
STUB
    cat > "$tmp/engine" <<'STUB'
#!/bin/sh
echo "engine $*" >> "$FAKE_LOG"
echo "ENGINE-DETAIL-MARKER"
exit "${FAKE_ENGINE_STATUS:-0}"
STUB
    # The effective Compose configuration: FAKE_DELIVERY is what Compose resolves for the backend.
    cat > "$tmp/fake-config" <<'STUB'
#!/bin/sh
[ "${FAKE_CONFIG_STATUS:-0}" = 0 ] || exit "$FAKE_CONFIG_STATUS"
case "$*" in
    *"config --format json"*)
        printf '{"services": {"backend": {"environment": {"BIOAF_LOG_DELIVERY": "%s"}}}}' "${FAKE_DELIVERY-stdout}" ;;
    *) exit 1 ;;
esac
STUB
    chmod +x "$tmp/bin/sudo" "$tmp/bin/docker" "$tmp/bin/curl" "$tmp/bin/systemctl" "$tmp/engine" "$tmp/fake-config"
    cat > "$tmp/cloud.sh" <<HARNESS
source '$BIOAF_SCRIPT' >/dev/null
export PATH='$tmp/bin':"\$PATH"
is_gce() { return 0; }
OPS_AGENT_CONFIG='$tmp/etc/config.yaml'
OPS_AGENT_ENGINE='$tmp/engine'
OPS_AGENT_DETAIL_DIR='$tmp/host-only'
OPS_AGENT_ACTIVE_WAIT=0
DC='$tmp/fake-config'
HARNESS
}

@test "the legacy fixture is the config v0.6.3 shipped, by the hash ./bioaf recognizes" {
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; echo \"\$OPS_AGENT_LEGACY_SHA256\""
    [ "$output" = "$(shasum -a 256 < "$LEGACY_OPS_CONFIG" | cut -d' ' -f1)" ]
}

@test "a fresh GCE host gets the parser config validated, installed and the agent restarted" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    head -1 "$tmp/etc/config.yaml" | grep -q '^# bioaf-managed: ops-agent-config/v2 sha256='
    grep -q 'field: log' "$tmp/etc/config.yaml"
    grep -q '^engine .*-in .*config.yaml.bioaf-staged' "$tmp/log"
    grep -q '^systemctl restart google-cloud-ops-agent$' "$tmp/log"
    grep -q '^systemctl is-active --quiet google-cloud-ops-agent-fluent-bit$' "$tmp/log"
}

@test "a fresh GCE host where setup installs the agent replaces the package's default config" {
    # The agent package writes its own default config.yaml. bioAF installed the agent in this run, so
    # that file is the package's, not the operator's: it is kept aside and the parser config installed.
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    cat > "$tmp/bin/curl" <<STUB
#!/bin/sh
echo "curl \$*" >> "\$FAKE_LOG"
cat > add-google-cloud-ops-agent-repo.sh <<'INSTALL'
cp '$tmp/engine' '$tmp/installed-engine'
printf 'logging:\\n  receivers:\\n    syslog: {type: files, include_paths: [/var/log/messages, /var/log/syslog]}\\n' > '$tmp/etc/config.yaml'
INSTALL
STUB
    chmod +x "$tmp/bin/curl"
    run env FAKE_LOG="$tmp/log" bash -c "cd '$tmp' && source '$tmp/cloud.sh'; OPS_AGENT_ENGINE='$tmp/installed-engine'; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    head -1 "$tmp/etc/config.yaml" | grep -q '^# bioaf-managed: ops-agent-config/v2 sha256='
    grep -q 'syslog' "$tmp/etc/config.yaml.package-default"
    grep -q '^systemctl restart google-cloud-ops-agent$' "$tmp/log"
}

@test "an operator's config that existed before setup installed the agent is still kept" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    printf 'logging:\n  receivers:\n    mine: {type: files, include_paths: [/x/CUSTOM-MARKER]}\n' > "$tmp/etc/config.yaml"
    cp "$tmp/etc/config.yaml" "$tmp/before"
    cat > "$tmp/bin/curl" <<STUB
#!/bin/sh
echo "cp '$tmp/engine' '$tmp/installed-engine'" > add-google-cloud-ops-agent-repo.sh
STUB
    chmod +x "$tmp/bin/curl"
    run env FAKE_LOG="$tmp/log" bash -c "cd '$tmp' && source '$tmp/cloud.sh'; OPS_AGENT_ENGINE='$tmp/installed-engine'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/config.yaml" "$tmp/before"
    [ ! -e "$tmp/etc/config.yaml.package-default" ]
}

@test "an existing install's legacy config is migrated to the parser config" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    cp "$LEGACY_OPS_CONFIG" "$tmp/etc/config.yaml"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    grep -q 'field: log' "$tmp/etc/config.yaml"
    [[ "$output" == *"migrated"* ]]
}

@test "a current config is not rewritten and the agent is not restarted" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    : > "$tmp/log"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    ! grep -q 'systemctl restart' "$tmp/log"
}

@test "a custom agent config is kept, fails the step, and its content is not printed" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    printf 'logging:\n  receivers:\n    mine: {type: files, include_paths: [/x/CUSTOM-MARKER]}\n' > "$tmp/etc/config.yaml"
    cp "$tmp/etc/config.yaml" "$tmp/before"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/config.yaml" "$tmp/before"
    [[ "$output" != *"CUSTOM-MARKER"* ]] && [[ "$output" != *"field: log"* ]]
    [ -f "$tmp/host-only/ops-agent-config-v2.candidate" ]
    ! grep -q 'systemctl restart' "$tmp/log"
}

@test "a parser config the engine rejects is not installed and the agent keeps running" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    cp "$LEGACY_OPS_CONFIG" "$tmp/etc/config.yaml"
    run env FAKE_LOG="$tmp/log" FAKE_ENGINE_STATUS=1 bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/config.yaml" "$LEGACY_OPS_CONFIG"
    ! grep -q 'systemctl restart' "$tmp/log"
    [[ "$output" != *"ENGINE-DETAIL-MARKER"* ]]
    grep -q ENGINE-DETAIL-MARKER "$tmp/host-only/ops-agent-config-v2.log"
}

@test "an agent that does not come back on the parser config is restored to the previous config" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    cp "$LEGACY_OPS_CONFIG" "$tmp/etc/config.yaml"
    run env FAKE_LOG="$tmp/log" FAKE_ACTIVE_STATUS=1 bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/config.yaml" "$LEGACY_OPS_CONFIG"
    [ "$(grep -c '^systemctl restart' "$tmp/log")" -eq 2 ]
}

@test "off GCE nothing is installed or configured" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; is_gce() { return 1; }; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    [ ! -e "$tmp/etc/config.yaml" ] && [ ! -s "$tmp/log" ]
}

@test "update reconciles the agent config after checkout and before the services restart" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; ensure_cloud_logging() { echo cloud_logging >> '$tmp/log'; }; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    # Owner-approved (2026-11-24, plan 03A): the nginx-only recreate that pauses Work Node launches is not
    # the services restart, so it is left out of the ordering.
    [ "$(grep -E '^(git .* checkout |cloud_logging|up )' "$tmp/log" | grep -v -- '--no-deps nginx' | cut -d' ' -f1)" = "$(printf 'git\ncloud_logging\nup')" ]
}

@test "an unsettled retry reconciles the agent config before the services restart" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_STATUS_EXEC_FAILS=1 bash -c "
        source '$tmp/harness.sh'; ensure_cloud_logging() { echo cloud_logging >> '$tmp/log'; }; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    [ "$(grep -E '^(cloud_logging|up)' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'cloud_logging\nup')" ]
}

@test "a failed agent reconciliation stops the update before the services restart" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; ensure_cloud_logging() { echo 'RAW-AGENT-DETAIL'; return 1; }; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
    [ "$(cat "$tmp/state/backend")" = v1.0.0 ]
    grep -q '"step": "cloud_logging"' "$tmp/update-status/current.json"
    ! grep -q 'RAW-AGENT-DETAIL' "$tmp/update-status/current.json"
}

@test "start reconciles the agent config before starting services and stops if that fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    stubs="DC='$tmp/fake-dc --env-file $tmp/.env'; ensure_pinned_image_tag() { :; }; ensure_backups_dir() { :; }; wait_for_db() { :; }; cmd_status() { :; }; sleep() { :; }"
    run env FAKE_LOG="$tmp/log" bash -c "source '$BIOAF_SCRIPT' >/dev/null; $stubs
        ensure_cloud_logging() { echo cloud_logging >> '$tmp/log'; }; cmd_start"
    [ "$status" -eq 0 ]
    [ "$(grep -E '^(cloud_logging|up)' "$tmp/log" | head -2 | cut -d' ' -f1)" = "$(printf 'cloud_logging\nup')" ]
    : > "$tmp/log"
    run env FAKE_LOG="$tmp/log" bash -c "source '$BIOAF_SCRIPT' >/dev/null; $stubs
        ensure_cloud_logging() { return 1; }; cmd_start"
    [ "$status" -ne 0 ]
    ! grep -q '^up' "$tmp/log"
    [[ "$output" != *"All services started"* ]]
}

@test "direct delivery on a GCE host without an agent installs and configures nothing" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_DELIVERY=direct bash -c "source '$tmp/cloud.sh'; OPS_AGENT_ENGINE='$tmp/absent'; ensure_cloud_logging" </dev/null
    [ "$status" -eq 0 ]
    [ ! -e "$tmp/etc/config.yaml" ]
    ! grep -qE '^(curl|engine|systemctl)' "$tmp/log" 2>/dev/null
}

@test "direct delivery is rejected while the agent collects container logs" {
    for existing in legacy managed; do
        tmp="$(mktemp -d)"; cloud_env "$tmp"
        if [ "$existing" = legacy ]; then
            cp "$LEGACY_OPS_CONFIG" "$tmp/etc/config.yaml"
        else
            run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
            [ "$status" -eq 0 ]
        fi
        cp "$tmp/etc/config.yaml" "$tmp/before"; : > "$tmp/log"
        run env FAKE_LOG="$tmp/log" FAKE_DELIVERY=" Direct " bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
        [ "$status" -ne 0 ]
        [[ "$output" == *"BIOAF_LOG_DELIVERY=direct"* ]]
        [[ "$output" == *"ingested twice"* ]]
        cmp -s "$tmp/etc/config.yaml" "$tmp/before"
        ! grep -qE '^(engine|systemctl restart)' "$tmp/log"
    done
}

@test "an unknown delivery value or an unreadable Compose configuration stops before touching the agent" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_DELIVERY=agent bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"BIOAF_LOG_DELIVERY"* ]]
    run env FAKE_LOG="$tmp/log" FAKE_CONFIG_STATUS=1 bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
    [ "$status" -ne 0 ]
    [ ! -e "$tmp/etc/config.yaml" ]
    ! grep -qE '^(curl|engine|systemctl)' "$tmp/log" 2>/dev/null
}

@test "the delivery comes from the effective Compose configuration, env file and shell override included" {
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/docker"
    cp "$BATS_TEST_DIRNAME/../../docker/docker-compose.yml" "$tmp/docker/"
    if ! docker compose version >/dev/null 2>&1; then skip "docker compose is not available"; fi
    printf 'BIOAF_LOG_DELIVERY=direct\n' > "$tmp/.env"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='docker compose -f $tmp/docker/docker-compose.yml --env-file $tmp/.env'; effective_log_delivery"
    [ "$output" = direct ]
    run env BIOAF_LOG_DELIVERY=stdout bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='docker compose -f $tmp/docker/docker-compose.yml --env-file $tmp/.env'; effective_log_delivery"
    [ "$output" = stdout ]
    : > "$tmp/.env"
    run env -u BIOAF_LOG_DELIVERY bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='docker compose -f $tmp/docker/docker-compose.yml --env-file $tmp/.env'; effective_log_delivery"
    [ "$output" = stdout ]
}

# ---------------------------------------------------------------------------
# The first update from the previous release (plan 22). That release's own cmd_update runs from
# memory; the only new code it executes before restarting the services is the target's
# `install.sh ensure-encryption-key` (every updater since v0.13.1 runs it after checkout). The
# fixture is v2026.11.21's ./bioaf, verbatim.
# ---------------------------------------------------------------------------

OLD_BIOAF="$BATS_TEST_DIRNAME/fixtures/bioaf.v2026.11.21"

# old_update_env <tmp>: update_env, but driven by the previous release's updater, with the target's
# real install.sh and a target ./bioaf whose ensure-cloud-logging records the call.
old_update_env() {
    local tmp="$1" fixture="${2:-$OLD_BIOAF}"
    update_env "$tmp" v1.0.0
    sed -i.bak "s|source '$BIOAF_SCRIPT'|source '$fixture'|" "$tmp/harness.sh"
    cp "$BATS_TEST_DIRNAME/../../install.sh" "$tmp/install.sh"
    mkdir -p "$tmp/scripts"; cp "$BATS_TEST_DIRNAME/../../scripts/update_host_io.py" "$tmp/scripts/"
    mkdir -p "$tmp/installer"; cp "$BATS_TEST_DIRNAME/../../installer/host_prereqs.sh" "$tmp/installer/"
    cat > "$tmp/bioaf" <<'STUB'
#!/usr/bin/env bash
echo "target-bioaf $*" >> "$FAKE_LOG"
[ "$1" = ensure-update-agent ] && exit "${FAKE_AGENT_HOOK_STATUS:-${FAKE_HOOK_STATUS:-0}}"
exit "${FAKE_HOOK_STATUS:-0}"
STUB
    chmod +x "$tmp/bioaf"
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
}

# Owner-approved change (2026-11-24, plan 03A): an older updater no longer restarts the services.
# The target's hook prepares only the host update agent and refuses, so the backend that may hold
# older Work Nodes' sync keys keeps running; the retry runs the new updater and its safety check.
@test "every supported older updater only prepares the update helper and leaves the services running" {
    for fixture in v0.13.0 v0.13.1 v2026.11.20 v2026.11.21; do
        tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp" "$BATS_TEST_DIRNAME/fixtures/bioaf.$fixture"
        cp "$tmp/.env" "$tmp/env.before"
        run env -u BIOAF_IMAGE_TAG -u BIOAF_UPDATE_GATE FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
            source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
        [ "$status" -ne 0 ] || { echo "$fixture proceeded"; false; }
        # Host-only preparation: the agent, nothing else.
        grep -q '^target-bioaf ensure-update-agent' "$tmp/log"
        ! grep -q '^target-bioaf ensure-cloud-logging' "$tmp/log"
        ! grep -qE '^(pull|up |schema_upgrade)' "$tmp/log" || { echo "$fixture restarted"; false; }
        cmp -s "$tmp/.env" "$tmp/env.before" || { echo "$fixture changed docker/.env"; false; }
        [ "$(cat "$tmp/state/backend")" = v1.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v1.0.0 ]
        [[ "$output" == *"The update helper is ready. bioAF and your work nodes are still running. Retry the update to continue."* ]]
        # The older updater records its own fixed error; the prepared agent turns it into the instruction.
        grep -q '"status": "failed"' "$tmp/update-status/current.json"
        grep -q '"outcome": "prepared"' "$tmp/host-state/update-preparation.json"
        python3 -I "$tmp/scripts/update_host_io.py" normalize-prepared-failure "$tmp"
        grep -q 'Retry the update to continue.' "$tmp/update-status/current.json" || { echo "$fixture not normalized"; cat "$tmp/update-status/current.json"; false; }
    done
}

@test "an older updater whose helper preparation fails reports it and is never normalized" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp"
    run env -u BIOAF_IMAGE_TAG -u BIOAF_UPDATE_GATE FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_AGENT_HOOK_STATUS=1 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    ! grep -qE '^(pull|up |schema_upgrade)' "$tmp/log"
    [[ "$output" != *"The update helper is ready"* ]]
    grep -q '"outcome": "failed"' "$tmp/host-state/update-preparation.json"
    python3 -I "$tmp/scripts/update_host_io.py" normalize-prepared-failure "$tmp"
    ! grep -q 'Retry the update to continue.' "$tmp/update-status/current.json"
}

@test "a failed reconciliation stops the previous release's update before the services restart" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_HOOK_STATUS=1 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -ne 0 ]
    ! grep -qE '^(up|schema_upgrade)' "$tmp/log"
    [ "$(cat "$tmp/state/backend")" = v1.0.0 ]
    grep -q '"status": "failed"' "$tmp/update-status/current.json"
}

@test "the current updater reconciles once, under its own cloud_logging step" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp"
    sed -i.bak "s|source '$OLD_BIOAF'|source '$BIOAF_SCRIPT'|" "$tmp/harness.sh"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; ensure_cloud_logging() { echo cloud_logging >> '$tmp/log'; }; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    ! grep -q '^target-bioaf ensure-cloud-logging' "$tmp/log"
    [ "$(grep -c '^cloud_logging' "$tmp/log")" -eq 1 ]
}

@test "ensure-cloud-logging is a host command of ./bioaf" {
    tmp="$(mktemp -d)"; mkdir -p "$tmp/bin"
    printf '#!/bin/sh\nexit 7\n' > "$tmp/bin/curl"; chmod +x "$tmp/bin/curl"
    run env PATH="$tmp/bin:$PATH" bash "$BIOAF_SCRIPT" ensure-cloud-logging
    [ "$status" -eq 0 ]
    [[ "$output" != *"Unknown command"* ]]
}

# direct_run <tmp> [env...]: ensure_cloud_logging in direct delivery.
direct_run() {
    local tmp="$1"; shift
    run env FAKE_LOG="$tmp/log" FAKE_DELIVERY=direct "$@" bash -c "source '$tmp/cloud.sh'; ensure_cloud_logging" </dev/null
}

@test "direct delivery accepts only an absent, comment-only or operator-marked agent config" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    direct_run "$tmp"
    [ "$status" -eq 0 ]
    printf '# The agent package default: everything commented out.\n\n#logging:\n#  receivers: {}\n' > "$tmp/etc/config.yaml"
    direct_run "$tmp"
    [ "$status" -eq 0 ]
    printf '# bioaf-log-delivery: no-container-logs\nlogging:\n  receivers:\n    app: {type: files, include_paths: [/var/log/myapp/*.log]}\n' > "$tmp/etc/config.yaml"
    cp "$tmp/etc/config.yaml" "$tmp/before"
    direct_run "$tmp"
    [ "$status" -eq 0 ]
    cmp -s "$tmp/etc/config.yaml" "$tmp/before"
    ! grep -qE '^(curl|engine|systemctl)' "$tmp/log" 2>/dev/null
}

@test "direct delivery rejects any other agent config as unrecognized, whatever it collects" {
    for config in \
        'logging:\n  receivers:\n    c: {type: files, include_paths: [/var/lib/docker/containers/ab*/*.log]}\n' \
        'logging:\n  receivers:\n    app: {type: files, include_paths: [/var/log/myapp/*.log]}\n' \
        'metrics:\n  receivers: {}\n'; do
        tmp="$(mktemp -d)"; cloud_env "$tmp"
        printf "$config" > "$tmp/etc/config.yaml"
        cp "$tmp/etc/config.yaml" "$tmp/before"
        direct_run "$tmp"
        [ "$status" -ne 0 ]
        [[ "$output" == *"not recognized"* ]]
        [[ "$output" == *"bioaf-log-delivery: no-container-logs"* ]]
        cmp -s "$tmp/etc/config.yaml" "$tmp/before"
    done
}

@test "direct delivery rejects an agent config that cannot be inspected" {
    tmp="$(mktemp -d)"; cloud_env "$tmp"
    cp "$LEGACY_OPS_CONFIG" "$tmp/etc/config.yaml"
    direct_run "$tmp" FAKE_SUDO_FAIL=cat
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not be inspected"* ]]
    # A directory that cannot be searched cannot show that the config is absent.
    rm "$tmp/etc/config.yaml"; chmod 000 "$tmp/etc"
    direct_run "$tmp" FAKE_SUDO_FAIL=test
    chmod 755 "$tmp/etc"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not be inspected"* ]]
}

# ---------------------------------------------------------------------------
# Update-agent unit reconciliation and activation (plan 23). sudo, systemctl, systemd-analyze and
# systemd-run are stubs, so these prove outcomes and ordering on this machine, not systemd itself.
# ---------------------------------------------------------------------------

REPO_ROOT="$BATS_TEST_DIRNAME/../.."

# agent_env <tmp>: a checkout at <tmp>/repo (the files the reconciliation reads), stub privileged
# tools on PATH, and <tmp>/agent.sh, a harness that sources ./bioaf and points it at them.
#   FAKE_MAINPID            what `systemctl show -p MainPID` reports (default 0: not running)
#   FAKE_SHOW_STATUS        exit of `systemctl show` (default 0: it reports FAKE_MAINPID)
#   FAKE_ACTIVE_STATUS      exit of `systemctl is-active` (default 0)
#   FAKE_<OP>_STATUS        exit of systemctl daemon-reload/enable/start/restart (OP upper-cased,
#                           RELOAD for daemon-reload); FAKE_RESTART_FAILS_ONCE=1 fails only the first
#   FAKE_VERIFY_STATUS      exit of systemd-analyze verify;  FAKE_RUN_STATUS  exit of systemd-run
#   FAKE_DIES_AFTER_START=1  the agent is active on the first check after a start, then not
#   FAKE_RESTART_HOOK=<file>  restart first runs <file> with bash (to interleave events)
#   FAKE_RESTART_MARK=<file>  restart also logs whether <file> existed at that moment
#   FAKE_WAITER_ACTIVE_STATUS  exit of `systemctl is-active` for the waiter unit (default 3: none)
agent_env() {
    local tmp="$1"
    mkdir -p "$tmp/bin" "$tmp/etc" "$tmp/repo/docker" "$tmp/repo/scripts" "$tmp/repo/installer"
    cp "$REPO_ROOT/docker/bioaf-update-agent.service" "$tmp/repo/docker/"
    cp "$REPO_ROOT/scripts/update-agent.sh" "$REPO_ROOT/scripts/update_host_io.py" "$tmp/repo/scripts/"
    cp -R "$REPO_ROOT/installer/update-agent-legacy" "$tmp/repo/installer/"
    cp "$REPO_ROOT/installer/host_prereqs.sh" "$tmp/repo/installer/"
    printf '#!/bin/sh\n"$@"\n' > "$tmp/bin/sudo"
    cat > "$tmp/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
    show) [ "${FAKE_SHOW_STATUS:-0}" = 0 ] || exit "$FAKE_SHOW_STATUS"; echo "${FAKE_MAINPID:-0}" ;;
    is-active)
        [ "$3" = bioaf-update-agent-activation ] && exit "${FAKE_WAITER_ACTIVE_STATUS:-3}"
        if [ -e "$FAKE_LOG.started" ]; then
            # FAKE_DIES_AFTER_START=1: active on the first check after a start, then gone.
            if [ "${FAKE_DIES_AFTER_START:-0}" = 1 ]; then
                [ -e "$FAKE_LOG.checked" ] && exit 3
                : > "$FAKE_LOG.checked"
            fi
            exit 0
        fi
        exit "${FAKE_ACTIVE_STATUS:-0}" ;;
    daemon-reload) exit "${FAKE_RELOAD_STATUS:-0}" ;;
    enable) exit "${FAKE_ENABLE_STATUS:-0}" ;;
    start) [ "${FAKE_START_STATUS:-0}" = 0 ] && : > "$FAKE_LOG.started"; exit "${FAKE_START_STATUS:-0}" ;;
    restart)
        if [ -n "${FAKE_RESTART_HOOK:-}" ]; then bash "$FAKE_RESTART_HOOK"; fi
        if [ -n "${FAKE_RESTART_MARK:-}" ]; then
            if [ -e "$FAKE_RESTART_MARK" ]; then echo restart-after-updater; else echo restart-during-updater; fi >> "$FAKE_LOG"
        fi
        if [ "${FAKE_RESTART_FAILS_ONCE:-0}" = 1 ] && [ ! -e "$FAKE_LOG.restarted" ]; then : > "$FAKE_LOG.restarted"; exit 1; fi
        [ "${FAKE_RESTART_STATUS:-0}" = 0 ] && : > "$FAKE_LOG.started"
        exit "${FAKE_RESTART_STATUS:-0}" ;;
esac
STUB
    cat > "$tmp/bin/systemd-analyze" <<'STUB'
#!/usr/bin/env bash
echo "systemd-analyze $* name=${2##*/} first=$(head -1 "$2")" >> "$FAKE_LOG"
echo "VERIFY-DETAIL-MARKER"
exit "${FAKE_VERIFY_STATUS:-0}"
STUB
    cat > "$tmp/bin/systemd-run" <<'STUB'
#!/usr/bin/env bash
echo "systemd-run $*" >> "$FAKE_LOG"
exit "${FAKE_RUN_STATUS:-0}"
STUB
    chmod +x "$tmp/bin"/*
    cat > "$tmp/agent.sh" <<HARNESS
source '$BIOAF_SCRIPT' >/dev/null
export PATH='$tmp/bin':"\$PATH"
SCRIPT_DIR='$tmp/repo'
UPDATE_AGENT_UNIT='$tmp/etc/bioaf-update-agent.service'
UPDATE_AGENT_ACTIVE_WAIT=0
UPDATE_AGENT_SETTLE=0
HARNESS
}

# legacy_unit <tmp> <version>: the unit an earlier release's setup wrote for this checkout.
legacy_unit() {
    sed -e "s|__BIOAF_DIR__|$1/repo|g" -e "s|__BIOAF_USER__|$(whoami)|g" -e "s|__BIOAF_GROUP__|$(id -gn)|g" \
        "$REPO_ROOT/installer/update-agent-legacy/$2.service"
}

ensure_agent() {
    rm -f "$tmp/log.started" "$tmp/log.checked"
    run env FAKE_LOG="$tmp/log" "$@" bash -c "source '$tmp/agent.sh'; ensure_update_agent" </dev/null
}

record_agent_code() {
    python3 -I "$tmp/repo/scripts/update_host_io.py" record-agent-code "$tmp/repo"
}

@test "a fresh host gets the rendered unit verified, installed, enabled and started" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    ensure_agent
    [ "$status" -eq 0 ]
    unit="$tmp/etc/bioaf-update-agent.service"
    head -1 "$unit" | grep -q '^# bioaf-managed: update-agent-unit/v1 sha256='
    grep -qx "ExecStart=$tmp/repo/scripts/update-agent.sh" "$unit"
    grep -qx "WorkingDirectory=$tmp/repo" "$unit"
    grep -qx "User=$(whoami)" "$unit" && grep -qx "Group=$(id -gn)" "$unit"
    ! grep -q '__BIOAF_' "$unit"
    grep -q '^systemd-analyze verify .* name=bioaf-update-agent.service first=# bioaf-managed: update-agent-unit/v1' "$tmp/log"
    [ "$(grep -E '^systemctl (daemon-reload|enable|restart)' "$tmp/log")" = \
      "$(printf 'systemctl daemon-reload\nsystemctl enable bioaf-update-agent\nsystemctl restart bioaf-update-agent')" ]
}

@test "a unit an earlier release wrote for this checkout is migrated, not accepted for its paths" {
    local v
    for v in v0.6.5 v0.11.5; do
        tmp="$(mktemp -d)"; agent_env "$tmp"
        legacy_unit "$tmp" "$v" > "$tmp/etc/bioaf-update-agent.service"
        ensure_agent
        [ "$status" -eq 0 ]
        [[ "$output" == *"migrated"* ]]
        head -1 "$tmp/etc/bioaf-update-agent.service" | grep -q '^# bioaf-managed: update-agent-unit/v1'
        grep -q '^systemctl restart bioaf-update-agent' "$tmp/log"
    done
}

@test "a current unit with a current running agent is neither rewritten nor restarted" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    ensure_agent
    record_agent_code
    cp -p "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    : > "$tmp/log"
    ensure_agent
    [ "$status" -eq 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ! grep -qE '^(systemd-analyze|systemctl (daemon-reload|enable|start|restart))' "$tmp/log"
}

@test "a stopped agent with a current unit is started without rewriting the unit" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    ensure_agent; record_agent_code
    : > "$tmp/log"
    ensure_agent FAKE_ACTIVE_STATUS=3
    grep -q '^systemctl start bioaf-update-agent' "$tmp/log"
    ! grep -qE '^(systemd-analyze|systemctl daemon-reload)' "$tmp/log"
}

@test "a stopped agent that cannot be started fails the step" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    ensure_agent; record_agent_code
    ensure_agent FAKE_ACTIVE_STATUS=3 FAKE_START_STATUS=1
    [ "$status" -ne 0 ]
}

@test "a running agent older than the code on disk is restarted even when the unit is unchanged" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    ensure_agent; record_agent_code
    echo '# newer agent code' >> "$tmp/repo/scripts/update-agent.sh"
    : > "$tmp/log"
    ensure_agent
    [ "$status" -eq 0 ]
    grep -q '^systemctl restart bioaf-update-agent' "$tmp/log"
    ! grep -q '^systemctl daemon-reload' "$tmp/log"
}

@test "a custom unit is kept, fails the step, and its content is not printed" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 | sed 's|^RestartSec=5|RestartSec=30 # CUSTOM-MARKER|' > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    [[ "$output" != *"CUSTOM-MARKER"* ]] && [[ "$output" != *"ExecStart"* ]]
    [ -f "$tmp/repo/host-only/update-agent-unit-v1.candidate" ]
    ! grep -qE '^systemctl (daemon-reload|restart|start)' "$tmp/log"
}

@test "a unit rendered for another checkout or user is custom, not recognized" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 | sed "s|$tmp/repo|/opt/other-bioaf|g" > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
}

@test "an operator-managed unit is kept, and a stopped agent is still started" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    { echo '# bioaf-managed: operator'; legacy_unit "$tmp" v0.11.5; } > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    record_agent_code
    ensure_agent FAKE_ACTIVE_STATUS=3
    [ "$status" -eq 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    grep -q '^systemctl start bioaf-update-agent' "$tmp/log"
}

@test "a checkout path or account name the unit cannot carry safely is refused before anything is written" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    mv "$tmp/repo" "$tmp/my repo"
    sed -i.bak "s|$tmp/repo|$tmp/my repo|" "$tmp/agent.sh"
    ensure_agent
    [ "$status" -ne 0 ]
    [ ! -e "$tmp/etc/bioaf-update-agent.service" ]
    ! grep -q '^systemctl' "$tmp/log" 2>/dev/null
    tmp="$(mktemp -d)"; agent_env "$tmp"
    printf '#!/bin/sh\nprintf "evil\\nUser=root\\n"\n' > "$tmp/bin/whoami"; chmod +x "$tmp/bin/whoami"
    ensure_agent
    [ "$status" -ne 0 ]
    [ ! -e "$tmp/etc/bioaf-update-agent.service" ]
}

@test "a unit systemd-analyze rejects is not installed and the agent is not restarted" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent FAKE_VERIFY_STATUS=1
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ! grep -qE '^systemctl (daemon-reload|restart)' "$tmp/log"
    [[ "$output" != *"VERIFY-DETAIL-MARKER"* ]]
    grep -q VERIFY-DETAIL-MARKER "$tmp/repo/host-only/update-agent-unit-v1.log"
}

@test "a failed reload, enable or restart restores the previous unit and reactivates it" {
    local fail
    for fail in FAKE_RELOAD_STATUS=1 FAKE_ENABLE_STATUS=1 FAKE_RESTART_FAILS_ONCE=1; do
        tmp="$(mktemp -d)"; agent_env "$tmp"
        legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
        cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
        ensure_agent "$fail"
        [ "$status" -ne 0 ]
        [[ "$output" == *"activation_failed"* ]] || [[ "$output" == *"rollback_failed"* ]]
        cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    done
    # Only the restart failed once: the previous unit came back.
    [[ "$output" == *"activation_failed"* ]]
}

@test "when reactivating the previous unit also fails, the step reports a failed rollback" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    ensure_agent FAKE_RESTART_STATUS=1
    [ "$status" -ne 0 ]
    [[ "$output" == *"rollback_failed"* ]]
}

@test "the step's failure reaches callers that use it in a condition" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_VERIFY_STATUS=1 bash -c "source '$tmp/agent.sh'
        if ensure_update_agent >/dev/null; then echo IF-SAW-SUCCESS; fi
        ensure_update_agent >/dev/null || echo OR-SAW-FAILURE" </dev/null
    [[ "$output" != *"IF-SAW-SUCCESS"* ]]
    [[ "$output" == *"OR-SAW-FAILURE"* ]]
}

@test "off systemd nothing is installed" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$tmp/agent.sh'; command() { [ \"\$2\" = systemctl ] && return 1; builtin command \"\$@\"; }; ensure_update_agent" </dev/null
    [ "$status" -eq 0 ]
    [ ! -e "$tmp/etc/bioaf-update-agent.service" ]
}

# busy_agent <tmp> <seconds>: a stand-in agent process whose only child is an updater for <seconds>,
# then its poll sleep; touches <tmp>/updater-done when the updater ends. Sets AGENT_PID.
busy_agent() {
    bash -c "perl -e 'sleep $2'; : > '$1/updater-done'; while :; do sleep 1; done" </dev/null >/dev/null 2>&1 &
    AGENT_PID=$!
    sleep 0.3
}

# polling_agent <tmp>: a stand-in agent that polls every second and starts an updater (perl, 5 s) as
# soon as <tmp>/trigger appears, the way the real agent picks up a trigger. Sets AGENT_PID. (macOS
# pgrep does not list an exited, unreaped child, so a short poll sleep could vanish while held.)
polling_agent() {
    bash -c "while :; do if [ -e '$1/trigger' ]; then rm -f '$1/trigger'; perl -e 'sleep 5'; fi; sleep 1; done" </dev/null >/dev/null 2>&1 &
    AGENT_PID=$!
    sleep 0.3
}

# A restart hook that delivers a trigger at the worst moment (after any idle check, before the
# restart) and records whether the agent got to start an updater before being restarted.
race_hook() {
    cat > "$tmp/race-hook" <<HOOK
: > '$tmp/trigger'
sleep 1.5
if pgrep -P $AGENT_PID -x perl >/dev/null; then echo updater-started-before-restart; else echo no-updater-before-restart; fi >> '$tmp/log'
case "\$(ps -o stat= -p $AGENT_PID)" in *T*) echo restart-while-held ;; *) echo restart-while-running ;; esac >> '$tmp/log'
# A real restart replaces the agent, leaving the trigger to the new one; the stand-in is not replaced.
rm -f '$tmp/trigger'
HOOK
}

not_stopped() { case "$(ps -o stat= -p "$1")" in *T*) return 1 ;; esac; }

waiter_env() {
    # The checkout's own ./bioaf, with the unit path and wait times pointed at the test.
    sed -e "s|^UPDATE_AGENT_UNIT=.*|UPDATE_AGENT_UNIT='$tmp/etc/bioaf-update-agent.service'|" \
        -e "s|^UPDATE_AGENT_ACTIVE_WAIT=.*|UPDATE_AGENT_ACTIVE_WAIT=0|" -e "s|^UPDATE_AGENT_SETTLE=.*|UPDATE_AGENT_SETTLE=0|" \
        "$BIOAF_SCRIPT" > "$tmp/repo/bioaf"
    cp "$REPO_ROOT/scripts/update-agent-activate.sh" "$tmp/repo/scripts/"
    mkdir -p "$tmp/repo/installer"; cp "$REPO_ROOT/installer/managed_file.sh" "$REPO_ROOT/installer/output.sh" "$tmp/repo/installer/"
}

# run_waiter [env...]: the transient unit's command, as systemd runs it, against the stubs.
run_waiter() {
    rm -f "$tmp/log.started" "$tmp/log.checked"
    run env FAKE_LOG="$tmp/log" PATH="$tmp/bin:$PATH" "$@" \
        perl -e 'alarm 40; exec @ARGV' /bin/bash "$tmp/repo/scripts/update-agent-activate.sh" </dev/null
}

@test "an agent with an update in flight is left alone: nothing changes now and a waiter is scheduled" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    busy_agent "$tmp" 30
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent FAKE_MAINPID="$AGENT_PID"
    not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ! grep -qE '^(systemd-analyze|systemctl (daemon-reload|restart|start))' "$tmp/log"
    grep -q "^systemd-run --unit=bioaf-update-agent-activation .*--uid=$(whoami) --gid=$(id -gn) .*/scripts/update-agent-activate.sh\$" "$tmp/log"
    [[ "$output" == *"when that update finishes"* ]]
}

@test "a trigger arriving after the idle check cannot start an update before the restart" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    polling_agent "$tmp"; race_hook
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    ensure_agent FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_HOOK="$tmp/race-hook"
    not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -qx no-updater-before-restart "$tmp/log"
    grep -qx restart-while-held "$tmp/log"
    ! grep -q updater-started-before-restart "$tmp/log"
}

@test "an agent whose state cannot be read is not treated as stopped: nothing changes" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent FAKE_SHOW_STATUS=1
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ! grep -qE '^(systemd-analyze|systemctl (daemon-reload|restart|start))|^systemd-run' "$tmp/log"
    [[ "$output" == *"state"* ]]
}

@test "concurrent reconciliations are serialized: neither can resume the agent during the other's hold" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    polling_agent "$tmp"; race_hook
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    rm -f "$tmp/log.started" "$tmp/log.checked"
    env FAKE_LOG="$tmp/log" FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_HOOK="$tmp/race-hook" \
        bash -c "source '$tmp/agent.sh'; ensure_update_agent" </dev/null > "$tmp/first.out" 2>&1 &
    first=$!
    sleep 0.5
    env FAKE_LOG="$tmp/log" FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_HOOK="$tmp/race-hook" \
        bash -c "source '$tmp/agent.sh'; ensure_update_agent" </dev/null > "$tmp/second.out" 2>&1
    second_status=$?
    wait "$first"; first_status=$?
    not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    [ "$first_status" -eq 0 ] && [ "$second_status" -eq 0 ]
    [ "$(grep -c '^restart-while-held$' "$tmp/log")" -eq 2 ]
    ! grep -q 'updater-started-before-restart\|restart-while-running' "$tmp/log"
}

@test "a reconciliation interrupted while acquiring its hold leaves the agent running" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    polling_agent "$tmp"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    # A slow pgrep: the interruption lands after the agent is stopped, before the hold is decided.
    real_pgrep="$(command -v pgrep)"
    printf '#!/usr/bin/env bash\n: > "%s/in-pgrep"\nsleep 2\nexec %q "$@"\n' "$tmp" "$real_pgrep" > "$tmp/bin/pgrep"
    chmod +x "$tmp/bin/pgrep"
    env FAKE_LOG="$tmp/log" FAKE_MAINPID="$AGENT_PID" bash -c "source '$tmp/agent.sh'; ensure_update_agent" </dev/null >/dev/null 2>&1 &
    caller=$!
    for _ in $(seq 1 50); do [ -e "$tmp/in-pgrep" ] && break; sleep 0.1; done
    [ -e "$tmp/in-pgrep" ]
    ! not_stopped "$AGENT_PID"
    # Interrupt everything the caller started, as a terminal or systemd would.
    pkill -TERM -P "$caller" 2>/dev/null || true
    kill -TERM "$caller" 2>/dev/null || true
    sleep 3
    not_stopped "$AGENT_PID"
    ! grep -q '^systemctl restart' "$tmp/log"
}

@test "the agent is never left stopped, whether reconciliation succeeds or fails" {
    local fail
    for fail in "" FAKE_VERIFY_STATUS=1 FAKE_RESTART_STATUS=1; do
        tmp="$(mktemp -d)"; agent_env "$tmp"
        polling_agent "$tmp"
        legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
        ensure_agent FAKE_MAINPID="$AGENT_PID" $fail
        not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    done
}

@test "the waiter reconciles once the update has ended, holding the agent through the restart" {
    tmp="$(mktemp -d)"; agent_env "$tmp"; waiter_env
    busy_agent "$tmp" 3
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    run_waiter FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_MARK="$tmp/updater-done"
    not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    head -1 "$tmp/etc/bioaf-update-agent.service" | grep -q '^# bioaf-managed: update-agent-unit/v1'
    grep -q '^systemd-analyze verify' "$tmp/log"
    grep -qx restart-after-updater "$tmp/log"
    ! grep -q restart-during-updater "$tmp/log"
}

@test "the waiter's restart is health-checked and a unit that does not stay up is rolled back" {
    tmp="$(mktemp -d)"; agent_env "$tmp"; waiter_env
    busy_agent "$tmp" 1
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    run_waiter FAKE_MAINPID="$AGENT_PID" FAKE_DIES_AFTER_START=1
    kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -ne 0 ]
    [[ "$output" == *"activation_failed"* ]] || [[ "$output" == *"rollback_failed"* ]]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
}

@test "the waiter's restart cannot race a trigger either" {
    tmp="$(mktemp -d)"; agent_env "$tmp"; waiter_env
    polling_agent "$tmp"; race_hook
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    run_waiter FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_HOOK="$tmp/race-hook"
    not_stopped "$AGENT_PID"; kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -qx no-updater-before-restart "$tmp/log"
}

@test "the scheduled waiter is a fixed script that systemd can store: one line per argument, no \$ or %" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    busy_agent "$tmp" 30
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$@" > "%s/run-args"\n' "$tmp" > "$tmp/bin/systemd-run"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    ensure_agent FAKE_MAINPID="$AGENT_PID"
    kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    # systemd stores a transient unit's command in a unit file and re-reads it on daemon-reload: a
    # newline in an argument breaks it, and $ or % would be expanded.
    [ "$(tail -1 "$tmp/run-args")" = "$tmp/repo/scripts/update-agent-activate.sh" ]
    [ "$(sed -n '$=' "$tmp/run-args")" -eq "$(grep -c . "$tmp/run-args")" ]
    ! grep -q '[$%]' "$tmp/run-args"
}

@test "an agent that exits right after it starts is a failed activation, and the previous unit is restored" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    cp "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
    ensure_agent FAKE_DIES_AFTER_START=1
    [ "$status" -ne 0 ]
    cmp -s "$tmp/etc/bioaf-update-agent.service" "$tmp/before"
}

# ---------------------------------------------------------------------------
# Where the update-agent unit is reconciled (plan 23): setup after any checkout, start, and every
# updater through the target's install.sh hook. The unit is the agent's, not the services': its
# failure is reported but stops neither start nor an update.
# ---------------------------------------------------------------------------

@test "every updater runs the target's update-agent reconciliation, and its failure does not stop the update" {
    local updater
    for updater in "$OLD_BIOAF" "$BIOAF_SCRIPT"; do
        tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp"
        sed -i.bak "s|source '$OLD_BIOAF'|source '$updater'|" "$tmp/harness.sh"
        run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_AGENT_HOOK_STATUS=1 BIOAF_SKIP_RESTART_WARN=1 bash -c "
            source '$tmp/harness.sh'; ensure_cloud_logging() { :; }; cmd_update 2.0.0 --yes"
        if [ "$updater" = "$OLD_BIOAF" ]; then
            # Owner-approved change (2026-11-24, plan 03A): the older updater runs the reconciliation as its
            # one-time preparation and then stops before restarting anything.
            [ "$status" -ne 0 ]
            [ "$(grep -E '^(git .* checkout |target-bioaf ensure-update-agent|up )' "$tmp/log" | cut -d' ' -f1,2)" = \
              "$(printf 'git -C\ntarget-bioaf ensure-update-agent')" ]
            [ "$(cat "$tmp/state/backend")" = v1.0.0 ]
            [[ "$output" == *"update helper could not be prepared"* ]]
            continue
        fi
        [ "$status" -eq 0 ]
        # Owner-approved (2026-11-24, plan 03A): the nginx-only recreate is not the services restart.
        [ "$(grep -E '^(git .* checkout |target-bioaf ensure-update-agent|up )' "$tmp/log" | grep -v -- '--no-deps nginx' | cut -d' ' -f1,2)" = \
          "$(printf 'git -C\ntarget-bioaf ensure-update-agent\nup args=[--env-file')" ]
        [ "$(cat "$tmp/state/backend")" = v2.0.0 ]
        [[ "$output" == *"update agent"* ]]
    done
}

@test "ensure-update-agent is a host command of ./bioaf" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    cp "$BIOAF_SCRIPT" "$tmp/repo/bioaf"
    mkdir -p "$tmp/repo/installer"; cp "$REPO_ROOT/installer/managed_file.sh" "$REPO_ROOT/installer/output.sh" "$tmp/repo/installer/"
    # The real unit path is not writable here, so the reconciliation runs and reports its failure.
    run env FAKE_LOG="$tmp/log" PATH="$tmp/bin:$PATH" FAKE_VERIFY_STATUS=1 bash "$tmp/repo/bioaf" ensure-update-agent </dev/null
    [ "$status" -ne 0 ]
    [[ "$output" == *"Update agent unit not updated"* ]]
    [[ "$output" != *"Unknown command"* ]]
    run bash "$BIOAF_SCRIPT" help
    [[ "$output" != *"ensure-update-agent"* ]]
}

@test "start reconciles the update agent and still starts the services when that fails" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    stubs="DC='$tmp/fake-dc --env-file $tmp/.env'; ensure_pinned_image_tag() { :; }; ensure_backups_dir() { :; }; wait_for_db() { :; }; cmd_status() { :; }; sleep() { :; }; ensure_cloud_logging() { :; }"
    run env FAKE_LOG="$tmp/log" bash -c "source '$BIOAF_SCRIPT' >/dev/null; $stubs
        ensure_update_agent() { echo update_agent >> '$tmp/log'; return 1; }; cmd_start"
    [ "$status" -eq 0 ]
    grep -q '^update_agent' "$tmp/log"
    grep -q '^up' "$tmp/log"
    [[ "$output" == *"update agent"* ]]
}

@test "setup reconciles the update agent after checking out the requested version" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    mkdir -p "$tmp/docker/certs"; : > "$tmp/.env"; : > "$tmp/docker/certs/tls.crt"
    printf '#!/bin/sh\nexit 0\n' > "$tmp/install.sh"; chmod +x "$tmp/install.sh"
    printf '#!/bin/sh\necho Linux\n' > "$tmp/uname"; chmod +x "$tmp/uname"
    run env FAKE_LOG="$tmp/log" PATH="$tmp:$PATH" HOME="$tmp" bash -c "source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc --env-file $tmp/.env'; ENV_FILE='$tmp/.env'; SCRIPT_DIR='$tmp'
        io_init() { :; }; section() { :; }; io_finish() { :; }; fail_line() { echo \"\$*\"; }
        step() { echo \"step \$1\" >> '$tmp/log'; shift; [ \"\$1\" = -- ] && shift; \"\$@\"; }
        maybe_step() { step \"\$@\"; }
        docker() { return 0; }
        git() { [ \"\$3\" = checkout ] && echo checkout >> '$tmp/log'; return 0; }
        ensure_cloud_logging() { :; }; ensure_update_agent() { echo update_agent >> '$tmp/log'; }
        resolve_and_pull_images() { :; }; pin_image_tag() { :; }; ensure_backups_dir() { :; }; wait_for_db() { :; }
        get_access_url() { echo https://x; }
        cmd_setup --version 9.9.9" </dev/null
    [ "$(grep -E '^(checkout|update_agent)' "$tmp/log")" = "$(printf 'checkout\nupdate_agent')" ]
}

# ---------------------------------------------------------------------------
# Deployment generations and the install-wide lock (installer/deployments.sh, issue #110)
# ---------------------------------------------------------------------------

@test "update refuses while another update, build or setup holds the deployment lock" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    SCRIPT_DIR="$tmp" bash -c "source '$BATS_TEST_DIRNAME/../../installer/deployments.sh'; deploy_lock && sleep 30" &
    holder=$!
    for _ in $(seq 1 50); do [ -e "$tmp/host-state/deployment.lock" ] && break; sleep 0.1; done
    sleep 0.5
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    pkill -P "$holder" 2>/dev/null || true; kill "$holder" 2>/dev/null || true
    [ "$status" -eq 75 ]
    [[ "$output" == *"Another bioAF update, build or setup is running"* ]]
    ! grep -qE '^(pull|up|backup)' "$tmp/log" 2>/dev/null
    # The running operation's status is not overwritten by the refused one.
    [ ! -e "$tmp/update-status/current.json" ]
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
}

@test "a failed update leaves its candidate recorded for the next run to report" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_CHECK_STATUS=78 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 1 ]
    python3 -c "import json; c=json.load(open('$tmp/host-state/deployments.json'))['candidate']; assert c['operation']=='update' and c['tag']=='v2.0.0', c"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" FAKE_CHECK_STATUS=78 BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [[ "$output" == *"An earlier update to v2.0.0"* ]]
}

@test "build pins its tag only after the build succeeded" {
    tmp="$(mktemp -d)"
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_current_version() { echo 2.0.0; }; DC_BUILD=false; cmd_build"
    [ "$status" -ne 0 ]
    grep -q '^BIOAF_IMAGE_TAG=v1.0.0$' "$tmp/.env"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_current_version() { echo 2.0.0; }; DC_BUILD=true; cmd_build"
    [ "$status" -eq 0 ]
    grep -q '^BIOAF_IMAGE_TAG=v2.0.0$' "$tmp/.env"
    python3 -c "import json; c=json.load(open('$tmp/host-state/deployments.json'))['candidate']; assert c['operation']=='build' and c['tag']=='v2.0.0', c"
}

# ---------------------------------------------------------------------------
# Update operation identity and the installation metrics hook (ADR-075)
#
# Each update has an operation in host-state/deployments.json: a UUID, its original start and source
# version, its target, status and completion time. A retry of the same unfinished target keeps them.
# After readiness and final host completion the updater asks the backend to reserve the metrics report.
# ---------------------------------------------------------------------------

operation() {
    # operation <tmp> <field>: one field of the recorded update operation.
    python3 -c 'import json, sys; print((json.load(open(sys.argv[1])).get("update_operation") or {}).get(sys.argv[2]) or "")' \
        "$1/host-state/deployments.json" "$2"
}

status_field() {
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$1/update-status/current.json" "$2"
}

run_update() {
    # run_update <tmp> <target> [env...]: one update through the stubbed harness.
    local tmp="$1" target="$2"; shift 2
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 "$@" bash -c "
        source '$tmp/harness.sh'; cmd_update $target --yes"
}

@test "a successful update records its operation and then asks the backend to record the upgrade" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0
    [ "$status" -eq 0 ]

    id="$(operation "$tmp" id)"
    [[ "$id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]
    [ "$(operation "$tmp" status)" = completed ]
    [ "$(operation "$tmp" target)" = v2.0.0 ]
    [ "$(operation "$tmp" from_version)" = 1.0.0 ]
    [ -n "$(operation "$tmp" completed_at)" ]
    # Shared status publishes the operation and its completion evidence.
    [ "$(status_field "$tmp" operation_id)" = "$id" ]
    [ "$(status_field "$tmp" operation_started_at)" = "$(operation "$tmp" started_at)" ]
    [ "$(status_field "$tmp" operation_completed_at)" = "$(operation "$tmp" completed_at)" ]
    # The backend is asked once, after the database upgrade, with the operation's own facts.
    grep -q "^record_upgrade_metric args=.*--operation-id $id --from-version 1.0.0 --to-version 2.0.0 " "$tmp/log"
    [ "$(grep -E '^(schema_upgrade run|record_upgrade_metric)' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'schema_upgrade\nrecord_upgrade_metric')" ]
}

@test "a completed update records the infrastructure notice after the upgrade metric" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0
    [ "$status" -eq 0 ]
    grep -q "^record_infrastructure_notice args=.*--to-version 2.0.0" "$tmp/log"
    [ "$(grep -E '^(record_upgrade_metric|record_infrastructure_notice)' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'record_upgrade_metric\nrecord_infrastructure_notice')" ]
}

@test "a failed infrastructure notice does not change a completed update" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_NOTICE_STATUS=1
    [ "$status" -eq 0 ]
    [ "$(operation "$tmp" status)" = completed ]
    [[ "$output" == *"will retry at startup"* ]]
}

@test "a failed update records no infrastructure notice" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_RUN_STATUS=1 FAKE_SCHEMA_STATUS='{"settled": false}'
    [ "$status" -ne 0 ]
    ! grep -q record_infrastructure_notice "$tmp/log"
}

@test "a failed update keeps its operation unfinished and records no upgrade" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_RUN_STATUS=1 FAKE_SCHEMA_STATUS='{"settled": false}'
    [ "$status" -ne 0 ]
    [ "$(operation "$tmp" status)" = in_progress ]
    [ -z "$(operation "$tmp" completed_at)" ]
    ! grep -q record_upgrade_metric "$tmp/log"
}

@test "a readiness failure records no upgrade" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_READY=no BIOAF_READY_TIMEOUT_SECONDS=0
    [ "$status" -ne 0 ]
    [ "$(operation "$tmp" status)" = in_progress ]
    ! grep -q record_upgrade_metric "$tmp/log"
}

@test "a retry after the target image started keeps the operation's id, start and source version" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_RUN_STATUS=1 FAKE_SCHEMA_STATUS='{"settled": false}'
    [ "$status" -ne 0 ]
    [ "$(cat "$tmp/state/backend")" = v2.0.0 ]   # the target image runs; its database upgrade did not finish
    id="$(operation "$tmp" id)"; started="$(operation "$tmp" started_at)"
    sleep 1

    run_update "$tmp" 2.0.0 FAKE_SCHEMA_STATUS='{"settled": false}'
    [ "$status" -eq 0 ]
    [ "$(operation "$tmp" id)" = "$id" ]
    [ "$(operation "$tmp" started_at)" = "$started" ]
    [ "$(operation "$tmp" from_version)" = 1.0.0 ]
    [ "$(operation "$tmp" status)" = completed ]
    grep -q "^record_upgrade_metric args=.*--operation-id $id --from-version 1.0.0 --to-version 2.0.0 --started-at $started " "$tmp/log"
}

@test "an interrupted update whose target already runs settled is finished with readiness, not skipped" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_READY=no BIOAF_READY_TIMEOUT_SECONDS=0
    [ "$status" -ne 0 ]
    id="$(operation "$tmp" id)"

    run_update "$tmp" 2.0.0 FAKE_SCHEMA_STATUS='{"settled": true}'
    [ "$status" -eq 0 ]
    [[ "$output" != *"Nothing to update"* ]]
    [ "$(operation "$tmp" status)" = completed ]
    grep -q "^record_upgrade_metric args=.*--operation-id $id " "$tmp/log"
}

@test "a settled update with no unfinished operation is a no-op that records nothing" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v2.0.0
    printf 'BIOAF_IMAGE_TAG=v2.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_SCHEMA_STATUS='{"settled": true}'
    [ "$status" -eq 0 ]
    [[ "$output" == *"Nothing to update"* ]]
    [ ! -e "$tmp/host-state/deployments.json" ] || [ -z "$(operation "$tmp" id)" ]
    ! grep -q record_upgrade_metric "$tmp/log" 2>/dev/null
}

@test "a later update to a different target is a new operation" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0
    first="$(operation "$tmp" id)"
    run_update "$tmp" 3.0.0
    [ "$status" -eq 0 ]
    [ "$(operation "$tmp" id)" != "$first" ]
    [ "$(operation "$tmp" from_version)" = 2.0.0 ]
    [ "$(grep -c '^record_upgrade_metric' "$tmp/log")" = 2 ]
}

@test "a failed metrics record never changes the update's outcome" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    run_update "$tmp" 2.0.0 FAKE_RECORD_STATUS=1
    [ "$status" -eq 0 ]
    [ "$(status_field "$tmp" status)" = completed ]
    grep -q '^record_upgrade_metric' "$tmp/log"
}

@test "a damaged deployment record suppresses the report but never stops the update" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; update_env "$tmp" v1.0.0
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
    mkdir -m 700 "$tmp/host-state"; echo '{not json' > "$tmp/host-state/deployments.json"
    run_update "$tmp" 2.0.0
    [ "$status" -eq 0 ]
    [ "$(status_field "$tmp" status)" = completed ]
    [ "$(cat "$tmp/host-state/deployments.json")" = '{not json' ]
    ! grep -q record_upgrade_metric "$tmp/log"
}

@test "recording the operation keeps the rest of the deployment record" {
    tmp="$(mktemp -d)"
    mkdir -m 700 "$tmp/host-state"
    cat > "$tmp/host-state/deployments.json" <<'JSON'
{"format": 1, "current": {"tag": "v1.0.0", "backend": "sha256:b", "frontend": "sha256:f"}, "previous": null,
 "candidate": {"operation": "update", "tag": "v2.0.0", "started_at": "2026-01-01T00:00:00+00:00"}, "extra": 7}
JSON
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'; deploy_operation_start v2.0.0 1.0.0 && deploy_operation_complete v2.0.0"
    [ "$status" -eq 0 ]
    python3 - "$tmp/host-state/deployments.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["current"] == {"tag": "v1.0.0", "backend": "sha256:b", "frontend": "sha256:f"}, r
assert r["candidate"]["tag"] == "v2.0.0" and r["extra"] == 7, r
op = r["update_operation"]
assert op["status"] == "completed" and op["target"] == "v2.0.0" and op["from_version"] == "1.0.0", op
PY
}

@test "a completed operation's completion time never changes" {
    tmp="$(mktemp -d)"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'
        deploy_operation_start v2.0.0 1.0.0 >/dev/null; first=\$(deploy_operation_complete v2.0.0); sleep 1
        again=\$(deploy_operation_complete v2.0.0); [ \"\$first\" = \"\$again\" ] && [ -n \"\$first\" ]"
    [ "$status" -eq 0 ]
}

@test "a local build records no update operation and asks for no upgrade report" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "
        source '$BIOAF_SCRIPT' >/dev/null
        DC='$tmp/fake-dc'; DC_BUILD='$tmp/fake-dc build'; SCRIPT_DIR='$tmp'; ENV_FILE='$tmp/.env'
        get_current_version() { echo 2.0.0; }
        cmd_build"
    [ "$status" -eq 0 ]
    [ -z "$(operation "$tmp" id 2>/dev/null)" ]
    ! grep -q record_upgrade_metric "$tmp/log" 2>/dev/null
}

@test "reset-install-metrics-identity passes its arguments to the backend command" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"
    run env FAKE_LOG="$tmp/log" bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; cmd_reset_install_metrics_identity --confirm"
    [ "$status" -eq 0 ]
    grep -q '^reset_install_metrics_identity args=.*exec -T backend python -m app.cli.reset_install_metrics_identity --confirm' "$tmp/log"
    run bash "$BIOAF_SCRIPT" help
    [[ "$output" == *"reset-install-metrics-identity"* ]]
}

# ---------------------------------------------------------------------------
# register-outputs: the command's exit status reaches the caller
# ---------------------------------------------------------------------------

@test "register-outputs propagates a failing command's exit status" {
    tmp="$BATS_TEST_TMPDIR"
    printf '#!/bin/sh\necho "args: $*"\nexit 3\n' > "$tmp/fake-dc"
    chmod +x "$tmp/fake-dc"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; cmd_register_outputs --run-id 7"
    [ "$status" -eq 3 ]
    [[ "$output" == *"exec -T backend python -m app.cli.register_outputs --run-id 7"* ]]
}

@test "register-outputs succeeds when the command does" {
    tmp="$BATS_TEST_TMPDIR"
    printf '#!/bin/sh\nexit 0\n' > "$tmp/fake-dc"
    chmod +x "$tmp/fake-dc"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; cmd_register_outputs"
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Version guidance: releases are calendar versions, YYYY.M.N (tags add a leading v).
# ---------------------------------------------------------------------------

@test "help shows calendar-version usage and examples" {
    run bash "$BIOAF_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"setup [--version YYYY.M.N]"* ]]
    [[ "$output" == *"./bioaf setup --version 2026.11.21"* ]]
    [[ "$output" == *"./bioaf update 2026.11.21"* ]]
    [[ "$output" != *"X.Y.Z"* ]]
    [[ "$output" != *"0.8.1"* ]]
    [[ "$output" != *"0.7.0"* ]]
}

@test "setup --version without a value gives a calendar-version example" {
    run bash "$BIOAF_SCRIPT" setup --version </dev/null
    [ "$status" -eq 1 ]
    [[ "$output" == *"--version requires a value"* ]]
    [[ "$output" == *"2026.11.21"* ]]
    [[ "$output" != *"0.8.1"* ]]
}

@test "update rejects a malformed version with the calendar-version format" {
    for bad in 2026.11 latest 2026.11.21-rc1 'v' '1.2.3.4'; do
        run bash -c "source '$BIOAF_SCRIPT' >/dev/null; get_running_version() { echo 2026.11.20; }
            deploy_begin() { echo DEPLOY-BEGIN; return 1; }; cmd_update '$bad' --yes" </dev/null
        [ "$status" -eq 1 ]
        [[ "$output" == *"Invalid version format"* ]]
        [[ "$output" == *"YYYY.M.N"* ]]
        [[ "$output" != *"X.Y.Z"* ]]
        [[ "$output" != *"DEPLOY-BEGIN"* ]]
    done
}

@test "update accepts calendar versions and legacy three-number pins, with or without v" {
    for pin in 2026.11.21 v2026.11.21 0.8.1 v0.8.1; do
        want="${pin#v}"
        run bash -c "source '$BIOAF_SCRIPT' >/dev/null; get_running_version() { echo '$want'; }
            db_upgrade_settled() { return 0; }; deploy_operation_unfinished() { return 1; }; cmd_update '$pin' --yes" </dev/null
        [ "$status" -eq 0 ]
        [[ "$output" == *"Already running version $want"* ]]
    done
}

# ---------------------------------------------------------------------------
# Host python3 prerequisite. Commands that run python3 on the host check it before changing anything;
# commands that only reach the backend container, and help, work without it.
# ---------------------------------------------------------------------------

load host_python

# host_checkout <tmp>: a checkout at <tmp>/repo and logging stand-ins in <tmp>/stubs for every host
# tool these commands reach (docker, sudo, systemctl, systemd-run, sg, curl, git, a Linux uname).
#   FAKE_GCE=1           curl answers the GCE metadata server (otherwise every curl fails)
#   FAKE_DOCKER_INFO=1   `docker info` fails (the socket is not accessible to this user)
host_checkout() {
    local tmp="$1" tool
    mkdir -p "$tmp/repo/installer" "$tmp/repo/scripts" "$tmp/repo/docker/certs" "$tmp/stubs" "$tmp/home"
    cp "$BIOAF_SCRIPT" "$tmp/repo/bioaf"
    cp "$REPO_ROOT/installer/output.sh" "$REPO_ROOT/installer/deployments.sh" "$REPO_ROOT/installer/managed_file.sh" \
        "$REPO_ROOT/installer/host_prereqs.sh" "$tmp/repo/installer/"
    cp "$REPO_ROOT/scripts/update_host_io.py" "$REPO_ROOT/scripts/update-agent.sh" \
        "$REPO_ROOT/scripts/update-agent-activate.sh" "$tmp/repo/scripts/"
    cp "$REPO_ROOT/docker/bioaf-update-agent.service" "$tmp/repo/docker/"
    printf '#!/bin/sh\necho "install.sh $*" >> "%s/calls"\n' "$tmp" > "$tmp/repo/install.sh"
    chmod +x "$tmp/repo/install.sh"
    for tool in docker sudo systemctl systemd-run sg git; do
        printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\n[ "%s $1" = "docker info" ] && [ "${FAKE_DOCKER_INFO:-0}" = 1 ] && exit 1\nexit 0\n' \
            "$tool" "$tmp" "$tool" > "$tmp/stubs/$tool"
    done
    printf '#!/bin/sh\necho "curl $*" >> "%s/calls"\n[ "${FAKE_GCE:-0}" = 1 ] && exit 0\nexit 7\n' "$tmp" > "$tmp/stubs/curl"
    printf '#!/bin/sh\necho Linux\n' > "$tmp/stubs/uname"
    chmod +x "$tmp/stubs/"*
}

# in_checkout <tmp> <python kind> <command...>: run <command> in the checkout with only that python3.
in_checkout() {
    local tmp="$1" kind="$2"; shift 2
    run env PATH="$tmp/stubs:$(host_python_path "$BATS_FILE_TMPDIR/py" "$kind")" HOME="$tmp/home" "$@" </dev/null
}

# The diagnostic every refused command gives.
refused_for_python() {
    [ "$status" -ne 0 ]
    [[ "$output" == *"python3"* ]]
    [[ "$output" == *"3.10"* ]]
}

# Nothing but the prerequisite check ran: no host tool, no file or directory created or changed.
nothing_changed() {
    local tmp="$1"
    [ ! -s "$tmp/calls" ] || { echo "host tools ran:"; cat "$tmp/calls"; return 1; }
    [ "$(cd "$tmp/repo" && find . -newer "$tmp/marker" | grep -v '^\.$' || true)" = "" ] || {
        echo "changed:"; (cd "$tmp/repo" && find . -newer "$tmp/marker"); return 1; }
    [ ! -e "$tmp/home/.bioaf" ]
}

mark_unchanged() { sleep 1; touch "$1/marker"; sleep 1; }

@test "help, unknown commands, status, stop and logs work without host python3" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" help
    [ "$status" -eq 0 ]; [[ "$output" == *"Usage:"* ]]
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" notarealcommand
    [ "$status" -eq 1 ]; [[ "$output" == *"Unknown command"* ]]; [[ "$output" != *"python3"* ]]
    for cmd in status stop "logs backend"; do
        : > "$tmp/calls"
        in_checkout "$tmp" none bash "$tmp/repo/bioaf" $cmd
        [ "$status" -eq 0 ]
        grep -q '^docker compose' "$tmp/calls"
    done
}

@test "start, restart, build and update refuse to run without a usable python3 and change nothing" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    printf 'POSTGRES_PASSWORD=keep\n' > "$tmp/repo/docker/.env"
    mark_unchanged "$tmp"
    for kind in none broken old nocap; do
        for cmd in start restart build "build backend" update "update 2026.11.21"; do
            in_checkout "$tmp" "$kind" bash "$tmp/repo/bioaf" $cmd
            refused_for_python
            nothing_changed "$tmp"
        done
    done
}

@test "start with a working python3 still starts the services" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    printf 'BIOAF_IMAGE_TAG=v2026.11.21\n' > "$tmp/repo/docker/.env"
    in_checkout "$tmp" real env BIOAF_ENV_FILE="$tmp/repo/docker/.env" bash -c "source '$tmp/repo/bioaf' >/dev/null; sleep() { :; }; cmd_start"
    [ "$status" -eq 0 ]
    grep -q '^docker compose .* up -d db' "$tmp/calls"
    grep -q '^docker compose .* up -d nginx' "$tmp/calls"
}

@test "setup without python3 stops before the install log, docker-group recovery or installation" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    mark_unchanged "$tmp"
    in_checkout "$tmp" none env FAKE_DOCKER_INFO=1 bash "$tmp/repo/bioaf" setup
    refused_for_python
    nothing_changed "$tmp"
}

@test "a repeat setup without python3 leaves the existing env and certificates alone" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    printf 'POSTGRES_PASSWORD=keep\n' > "$tmp/repo/docker/.env"; : > "$tmp/repo/docker/certs/tls.crt"
    mark_unchanged "$tmp"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" setup --version 2026.11.21
    refused_for_python
    nothing_changed "$tmp"
}

@test "setup without python3 still reports bad arguments and unsupported operating systems first" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" setup --version
    [ "$status" -eq 1 ]; [[ "$output" == *"--version requires a value"* ]]
    printf '#!/bin/sh\necho Darwin\n' > "$tmp/stubs/uname"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" setup
    [ "$status" -eq 1 ]; [[ "$output" == *"cannot be installed on macOS"* ]]
    [ ! -e "$tmp/home/.bioaf" ]
}

@test "the cloud logging hook needs python3 only on GCE, and changes nothing without it there" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" ensure-cloud-logging
    [ "$status" -eq 0 ]
    mark_unchanged "$tmp"; : > "$tmp/calls"
    in_checkout "$tmp" none env FAKE_GCE=1 bash "$tmp/repo/bioaf" ensure-cloud-logging
    refused_for_python
    ! grep -qvE '^curl ' "$tmp/calls"
}

@test "the update agent hook without python3 changes nothing" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    mark_unchanged "$tmp"
    in_checkout "$tmp" none bash "$tmp/repo/bioaf" ensure-update-agent
    refused_for_python
    nothing_changed "$tmp"
}

@test "the update agent activation waiter without python3 exits at once and changes nothing" {
    tmp="$(mktemp -d)"; host_checkout "$tmp"
    mark_unchanged "$tmp"
    in_checkout "$tmp" none env UPDATE_AGENT_WAIT_LIMIT=4 bash "$tmp/repo/scripts/update-agent-activate.sh"
    refused_for_python
    nothing_changed "$tmp"
}

# ---------------------------------------------------------------------------
# Approved custom Git server keys (plan 03C)
# ---------------------------------------------------------------------------

@test "import-git-known-hosts sends the file to the backend import with its source" {
    tmp="$(mktemp -d)"
    printf '#!/usr/bin/env bash\necho "dc $*" >> "%s/log"\ncat >> "%s/stdin"\n' "$tmp" "$tmp" > "$tmp/fake-dc"; chmod +x "$tmp/fake-dc"
    printf 'git.example.org ssh-ed25519 AAAAkey\n' > "$tmp/hosts"
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; DC='$tmp/fake-dc'; cmd_import_git_known_hosts '$tmp/hosts' 'from the server admin'"
    [ "$status" -eq 0 ]
    grep -q 'exec -T backend python -m app.cli.import_git_known_hosts /dev/stdin --source from the server admin' "$tmp/log"
    grep -q 'git.example.org ssh-ed25519 AAAAkey' "$tmp/stdin"
}

@test "import-git-known-hosts needs a readable file and a source" {
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; cmd_import_git_known_hosts /nonexistent 'x'"
    [ "$status" -ne 0 ]
    run bash -c "source '$BIOAF_SCRIPT' >/dev/null; cmd_import_git_known_hosts '$BIOAF_SCRIPT'"
    [ "$status" -ne 0 ]
    run bash "$BIOAF_SCRIPT" help
    [[ "$output" == *"import-git-known-hosts"* ]]
}

@test "ssh-recovery-diagnostics collects fixed facts and publishes them through the host program" {
    tmp="$(mktemp -d)"; mkdir -p "$tmp/bin" "$tmp/scripts"
    cp "$BATS_TEST_DIRNAME/../../scripts/update_host_io.py" "$tmp/scripts/"
    printf '#!/bin/sh\ncase "$*" in *instance/id*) echo 481516;; *project/project-id*) echo lab;; *) exit 22;; esac\n' > "$tmp/bin/curl"
    printf '#!/bin/sh\n[ "$1" = -n ] && shift; case "$*" in *"sshd -t"*) exit 0;; *"sshd -T"*) printf "pubkeyauthentication yes\\nauthorizedkeyscommand none\\n";; esac\n' > "$tmp/bin/sudo"
    printf '#!/bin/sh\n[ "$2" = google-guest-agent ] && echo active || echo inactive\n' > "$tmp/bin/systemctl"
    printf '#!/bin/sh\nexit 1\n' > "$tmp/bin/dpkg"
    chmod +x "$tmp/bin/"*
    run env PATH="$tmp/bin:$PATH" bash -c "source '$BIOAF_SCRIPT' >/dev/null; SCRIPT_DIR='$tmp'; HOST_IO_PY=\"\$(cat '$tmp/scripts/update_host_io.py')\"; cmd_ssh_recovery_diagnostics"
    [ "$status" -eq 0 ]
    python3 -c "import json; d=json.load(open('$tmp/update-status/ssh-recovery-diagnostics.json')); assert d['provider']=='gcp' and d['instance_id']=='481516' and d['guest_agent'] is True and d['pubkey_auth'] is True and d['eic_package'] is False, d"
}
