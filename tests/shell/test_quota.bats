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
case "$1 $2 $3" in
    "auth print-access-token "*)
        echo "ya29.fake-token"
        ;;
    "alpha quotas info")
        # `gcloud alpha quotas info describe <quota_id> --service=... --project=... --format=json`
        # The 4th positional arg is the quota id when subcommand is "describe".
        if [ "$3" = "info" ] && [ "$4" = "describe" ]; then
            qid="$5"
            if [ -f "$FIXTURE_DIR/quota_info_${qid}.json" ]; then
                cat "$FIXTURE_DIR/quota_info_${qid}.json"
                exit 0
            fi
            # Default: empty dimensionsInfos so callers parse 0.
            echo '{"dimensionsInfos":[]}'
        fi
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

# ---------------------------------------------------------------------------
# bioaf_quota_get_current
# ---------------------------------------------------------------------------

@test "get_current returns the value for a project-scoped quota" {
    cat > "$FIXTURE_DIR/quota_info_CPUS-ALL-REGIONS-per-project.json" <<'JSON'
{
  "name": "projects/x/locations/global/services/compute.googleapis.com/quotaInfos/CPUS-ALL-REGIONS-per-project",
  "dimensionsInfos": [
    { "dimensions": {}, "details": { "value": "12" } }
  ]
}
JSON
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_get_current compute.googleapis.com CPUS-ALL-REGIONS-per-project my-proj"
    [ "$status" -eq 0 ]
    [ "$output" = "12" ]
}

@test "get_current returns the value for the matching region of a regional quota" {
    cat > "$FIXTURE_DIR/quota_info_SSD-TOTAL-GB-per-project-region.json" <<'JSON'
{
  "name": "projects/x/locations/global/services/compute.googleapis.com/quotaInfos/SSD-TOTAL-GB-per-project-region",
  "dimensionsInfos": [
    { "dimensions": {"region": "us-east1"}, "details": { "value": "500" } },
    { "dimensions": {"region": "us-central1"}, "details": { "value": "250" } },
    { "dimensions": {"region": "europe-west1"}, "details": { "value": "750" } }
  ]
}
JSON
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_get_current compute.googleapis.com SSD-TOTAL-GB-per-project-region my-proj us-central1"
    [ "$status" -eq 0 ]
    [ "$output" = "250" ]
}

@test "get_current returns 0 when the quota info has no matching region entry" {
    cat > "$FIXTURE_DIR/quota_info_SSD-TOTAL-GB-per-project-region.json" <<'JSON'
{ "dimensionsInfos": [
    { "dimensions": {"region": "europe-west1"}, "details": { "value": "999" } }
] }
JSON
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_get_current compute.googleapis.com SSD-TOTAL-GB-per-project-region my-proj us-central1"
    [ "$status" -eq 0 ]
    [ "$output" = "0" ]
}

@test "get_current invokes gcloud with the expected arguments" {
    run bash -c "source '$QUOTA_HELPER'; bioaf_quota_get_current compute.googleapis.com CPUS-ALL-REGIONS-per-project my-proj"
    [ "$status" -eq 0 ]
    grep -q "alpha quotas info describe CPUS-ALL-REGIONS-per-project" "$CALL_LOG"
    grep -q -- "--service=compute.googleapis.com" "$CALL_LOG"
    grep -q -- "--project=my-proj" "$CALL_LOG"
    grep -q -- "--format=json" "$CALL_LOG"
}
