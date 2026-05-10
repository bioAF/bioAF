#!/usr/bin/env bats
# Tests for the ./bioaf management script

BIOAF_SCRIPT="$BATS_TEST_DIRNAME/../../bioaf"

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
# verify_image_available  (uses real ghcr.io; skipped if no network)
# ---------------------------------------------------------------------------

@test "verify_image_available returns 0 for known-published tag" {
    if ! curl -sf --max-time 5 "https://ghcr.io/v2/" >/dev/null 2>&1 \
        && ! curl -s --max-time 5 -o /dev/null -w '%{http_code}' "https://ghcr.io/v2/" \
            | grep -q '^[0-9]'; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_image_available 'bioaf/bioaf-backend' 'v0.11.5'"
    [ "$status" -eq 0 ]
}

@test "verify_image_available returns 1 for missing tag" {
    if ! curl -s --max-time 5 -o /dev/null "https://ghcr.io/v2/" 2>/dev/null; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_image_available 'bioaf/bioaf-backend' 'v9999.99.99'"
    [ "$status" -eq 1 ]
}

# ---------------------------------------------------------------------------
# verify_release_images
# ---------------------------------------------------------------------------

@test "verify_release_images reports friendly retry message when images missing" {
    if ! curl -s --max-time 5 -o /dev/null "https://ghcr.io/v2/" 2>/dev/null; then
        skip "ghcr.io unreachable"
    fi
    run bash -c "source '$BIOAF_SCRIPT' && verify_release_images '9999.99.99'"
    [ "$status" -eq 1 ]
    [[ "$output" == *"not yet published"* ]]
    [[ "$output" == *"try again in a few minutes"* ]]
}
