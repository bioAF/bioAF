#!/usr/bin/env bats
# Tests for installer/quota.sh -- the Cloud Quotas auto-request helper used
# by install-gcp.sh.
#
# These tests run in isolation: they prepend a stubs directory to PATH so any
# `gcloud` / `curl` calls made by the helper hit fixture-driven fakes instead
# of real GCP. Each test asserts on either the helper's stdout or the call
# log written by the stubs.

QUOTA_HELPER="$BATS_TEST_DIRNAME/../../installer/quota.sh"

setup() {
    TEST_DIR="$(mktemp -d)"
    export STUBS_DIR="$TEST_DIR/stubs"
    export CALL_LOG="$TEST_DIR/calls.log"
    export FIXTURE_DIR="$TEST_DIR/fixtures"
    mkdir -p "$STUBS_DIR" "$FIXTURE_DIR"
    : > "$CALL_LOG"

    # gcloud stub: logs invocation, then dispatches on subcommand to a fixture
    # file the test has staged ahead of time.
    cat > "$STUBS_DIR/gcloud" <<'STUB'
#!/usr/bin/env bash
printf 'gcloud %s\n' "$*" >> "$CALL_LOG"
case "$1 $2" in
    "auth print-access-token")
        echo "ya29.fake-token"
        ;;
    "alpha quotas")
        # `gcloud alpha quotas info describe ... --format=...`
        if [ -f "$FIXTURE_DIR/quota_info_$4.json" ]; then
            cat "$FIXTURE_DIR/quota_info_$4.json"
            exit 0
        fi
        # Default: emit a benign empty quota response so callers parse 0.
        echo '{"effectiveLimit":"0"}'
        ;;
    *) ;;
esac
exit 0
STUB
    chmod +x "$STUBS_DIR/gcloud"

    # curl stub: logs the invocation and serves a fixture body keyed by a
    # marker the test sets via $CURL_FIXTURE.
    cat > "$STUBS_DIR/curl" <<'STUB'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$CALL_LOG"
if [ -n "${CURL_FIXTURE:-}" ] && [ -f "$FIXTURE_DIR/$CURL_FIXTURE" ]; then
    cat "$FIXTURE_DIR/$CURL_FIXTURE"
fi
exit 0
STUB
    chmod +x "$STUBS_DIR/curl"

    export PATH="$STUBS_DIR:$PATH"
}

teardown() {
    rm -rf "$TEST_DIR"
}

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

@test "quota.sh exists and is sourceable" {
    [ -f "$QUOTA_HELPER" ]
    run bash -c "source '$QUOTA_HELPER'"
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Targets table
# ---------------------------------------------------------------------------

@test "bioaf_quota_targets emits the three required quota rows" {
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_targets"
    [ "$status" -eq 0 ]
    # Each row: service<TAB>quota_id<TAB>scope<TAB>preferred_value
    [[ "$output" == *"compute.googleapis.com"*"CPUS-ALL-REGIONS-per-project"*"project"*"64"* ]]
    [[ "$output" == *"compute.googleapis.com"*"SSD-TOTAL-GB-per-project-region"*"region"*"1024"* ]]
    [[ "$output" == *"compute.googleapis.com"*"DISKS-TOTAL-GB-per-project-region"*"region"*"2048"* ]]
}

@test "bioaf_quota_targets emits exactly three rows" {
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_targets | wc -l"
    [ "$status" -eq 0 ]
    [ "$(echo "$output" | tr -d ' ')" = "3" ]
}
