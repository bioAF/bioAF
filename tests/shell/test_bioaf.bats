#!/usr/bin/env bats
# Tests for the ./bioaf management script

BIOAF_SCRIPT="$BATS_TEST_DIRNAME/../../bioaf"

load network

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
    [ "$(grep -E '^(pull|check|up|schema_upgrade run|backup)' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'backup\npull\ncheck\nup\nschema_upgrade')" ]
    [ "$(cat "$tmp/state/backend")" = v2.0.0 ] && [ "$(cat "$tmp/state/frontend")" = v2.0.0 ]
    grep -q '"status": "completed"' "$tmp/update-status/current.json"
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
    printf '#!/bin/sh\n"$@"\n' > "$tmp/bin/sudo"
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
    chmod +x "$tmp/bin/sudo" "$tmp/bin/curl" "$tmp/bin/systemctl" "$tmp/engine"
    cat > "$tmp/cloud.sh" <<HARNESS
source '$BIOAF_SCRIPT' >/dev/null
export PATH='$tmp/bin':"\$PATH"
is_gce() { return 0; }
OPS_AGENT_CONFIG='$tmp/etc/config.yaml'
OPS_AGENT_ENGINE='$tmp/engine'
OPS_AGENT_DETAIL_DIR='$tmp/host-only'
OPS_AGENT_ACTIVE_WAIT=0
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
    [ "$(grep -E '^(git .* checkout |cloud_logging|up )' "$tmp/log" | cut -d' ' -f1)" = "$(printf 'git\ncloud_logging\nup')" ]
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
