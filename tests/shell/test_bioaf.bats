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
    local tmp="$1"
    update_env "$tmp" v1.0.0
    sed -i.bak "s|source '$BIOAF_SCRIPT'|source '$OLD_BIOAF'|" "$tmp/harness.sh"
    cp "$BATS_TEST_DIRNAME/../../install.sh" "$tmp/install.sh"
    cat > "$tmp/bioaf" <<'STUB'
#!/usr/bin/env bash
echo "target-bioaf $*" >> "$FAKE_LOG"
[ "$1" = ensure-update-agent ] && exit "${FAKE_AGENT_HOOK_STATUS:-${FAKE_HOOK_STATUS:-0}}"
exit "${FAKE_HOOK_STATUS:-0}"
STUB
    chmod +x "$tmp/bioaf"
    printf 'BIOAF_IMAGE_TAG=v1.0.0\n' > "$tmp/.env"
}

@test "the previous release's updater reconciles the agent config before the new backend starts" {
    tmp="$(mktemp -d)"; make_fake_dc "$tmp"; old_update_env "$tmp"
    run env -u BIOAF_IMAGE_TAG FAKE_LOG="$tmp/log" FAKE_STATE="$tmp/state" BIOAF_SKIP_RESTART_WARN=1 bash -c "
        source '$tmp/harness.sh'; cmd_update 2.0.0 --yes"
    [ "$status" -eq 0 ]
    [ "$(grep -E '^(git .* checkout |target-bioaf|pull|up )' "$tmp/log" | cut -d' ' -f1,2)" = \
      "$(printf 'git -C\ntarget-bioaf ensure-cloud-logging\ntarget-bioaf ensure-update-agent\npull tag=v2.0.0\nup args=[--env-file')" ]
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
#   FAKE_ACTIVE_STATUS      exit of `systemctl is-active` (default 0)
#   FAKE_<OP>_STATUS        exit of systemctl daemon-reload/enable/start/restart (OP upper-cased,
#                           RELOAD for daemon-reload); FAKE_RESTART_FAILS_ONCE=1 fails only the first
#   FAKE_VERIFY_STATUS      exit of systemd-analyze verify;  FAKE_RUN_STATUS  exit of systemd-run
#   FAKE_RESTART_MARK=<file>  restart also logs whether <file> existed at that moment
#   FAKE_WAITER_ACTIVE_STATUS  exit of `systemctl is-active` for the waiter unit (default 3: none)
agent_env() {
    local tmp="$1"
    mkdir -p "$tmp/bin" "$tmp/etc" "$tmp/repo/docker" "$tmp/repo/scripts" "$tmp/repo/installer"
    cp "$REPO_ROOT/docker/bioaf-update-agent.service" "$tmp/repo/docker/"
    cp "$REPO_ROOT/scripts/update-agent.sh" "$REPO_ROOT/scripts/update_host_io.py" "$tmp/repo/scripts/"
    cp -R "$REPO_ROOT/installer/update-agent-legacy" "$tmp/repo/installer/"
    printf '#!/bin/sh\n"$@"\n' > "$tmp/bin/sudo"
    cat > "$tmp/bin/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
    show) echo "${FAKE_MAINPID:-0}" ;;
    is-active)
        [ "$3" = bioaf-update-agent-activation ] && exit "${FAKE_WAITER_ACTIVE_STATUS:-3}"
        [ -e "$FAKE_LOG.started" ] && exit 0; exit "${FAKE_ACTIVE_STATUS:-0}" ;;
    daemon-reload) exit "${FAKE_RELOAD_STATUS:-0}" ;;
    enable) exit "${FAKE_ENABLE_STATUS:-0}" ;;
    start) [ "${FAKE_START_STATUS:-0}" = 0 ] && : > "$FAKE_LOG.started"; exit "${FAKE_START_STATUS:-0}" ;;
    restart)
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
HARNESS
}

# legacy_unit <tmp> <version>: the unit an earlier release's setup wrote for this checkout.
legacy_unit() {
    sed -e "s|__BIOAF_DIR__|$1/repo|g" -e "s|__BIOAF_USER__|$(whoami)|g" -e "s|__BIOAF_GROUP__|$(id -gn)|g" \
        "$REPO_ROOT/installer/update-agent-legacy/$2.service"
}

ensure_agent() {
    rm -f "$tmp/log.started"
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

@test "an agent with an update in flight is not restarted now; a waiter outside it is scheduled" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    busy_agent "$tmp" 30
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    ensure_agent FAKE_MAINPID="$AGENT_PID"
    kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    head -1 "$tmp/etc/bioaf-update-agent.service" | grep -q '^# bioaf-managed: update-agent-unit/v1'
    grep -q '^systemctl daemon-reload' "$tmp/log"
    ! grep -q '^systemctl restart' "$tmp/log"
    grep -q '^systemd-run --unit=bioaf-update-agent-activation ' "$tmp/log"
    [ "$(tail -1 "$tmp/log")" = "_ $AGENT_PID" ] || [[ "$(tail -1 "$tmp/log")" == *" _ $AGENT_PID" ]]
}

@test "the waiter restarts the agent only after its in-flight updater has ended" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    busy_agent "$tmp" 3
    run env FAKE_LOG="$tmp/log" FAKE_MAINPID="$AGENT_PID" FAKE_RESTART_MARK="$tmp/updater-done" perl -e 'alarm 30; exec @ARGV' bash -c "source '$tmp/agent.sh'; update_agent_restart_when_idle $AGENT_PID 20"
    kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -q '^restart-after-updater$' "$tmp/log"
    ! grep -q 'restart-during-updater' "$tmp/log"
}

@test "the waiter leaves an agent that something else already restarted alone" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    run env FAKE_LOG="$tmp/log" FAKE_MAINPID=424242 bash -c "source '$tmp/agent.sh'; update_agent_restart_when_idle 1 5" </dev/null
    [ "$status" -eq 0 ]
    ! grep -q '^systemctl restart' "$tmp/log"
}

@test "the scheduled waiter carries no data in its program text" {
    tmp="$(mktemp -d)"; agent_env "$tmp"
    busy_agent "$tmp" 30
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$@" > "%s/run-args"\n' "$tmp" > "$tmp/bin/systemd-run"
    legacy_unit "$tmp" v0.11.5 > "$tmp/etc/bioaf-update-agent.service"
    ensure_agent FAKE_MAINPID="$AGENT_PID"
    kill "$AGENT_PID" 2>/dev/null || true
    [ "$status" -eq 0 ]
    # The last argument is the agent PID; the program before it is the same for any PID.
    [ "$(tail -1 "$tmp/run-args")" = "$AGENT_PID" ]
    ! sed '$d' "$tmp/run-args" | grep -q "$AGENT_PID"
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
        [ "$status" -eq 0 ]
        [ "$(grep -E '^(git .* checkout |target-bioaf ensure-update-agent|up )' "$tmp/log" | cut -d' ' -f1,2)" = \
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
